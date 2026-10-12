"""Recordatorios: interpretar, confirmar, guardar y avisar con insistencia.

Flujo: el canal llama a interpret() con su chat; lo entendido queda en pending_confirmations
(en la base, con caducidad, así sobrevive a reinicios del canal). Solo si el usuario confirma,
confirm() escribe la fila en reminders y la devuelve leída de la base. El programador (worker) deja los avisos vencidos en el buzón de salida
(outbox) y los repite cada NEXUS_REMINDER_RETRY_MIN minutos hasta NEXUS_REMINDER_MAX_TRIES
avisos o hasta que el usuario responda /hecho.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from config import settings
from core import clock, events, outbox, when
from core.db import connect
from providers.factory import get_provider

log = logging.getLogger("nexus.reminders")

LLM_PROMPT = (
    "Hoy es {hoy} y son las {hora}. Del siguiente pedido de recordatorio extrae la fecha, "
    "la hora y la tarea. Responde SOLO con JSON así: "
    '{{"fecha": "AAAA-MM-DD", "hora": "HH:MM", "tarea": "..."}}. '
    'Si no hay ninguna fecha u hora, responde {{"fecha": null}}.\n\nPedido: {texto}'
)


class NotUnderstood(ValueError):
    pass


def _result(dt_local: datetime, task: str, method: str, now: datetime) -> dict:
    if dt_local <= now:
        raise NotUnderstood(
            f"La fecha entendida ({when.describe(dt_local, now)}) ya pasó. Dímela de nuevo."
        )
    if not task:
        raise NotUnderstood("Entendí la fecha, pero no qué debo recordarte.")
    return {
        "due_at": clock.local_to_utc(dt_local).isoformat(),
        "due_local": dt_local.strftime("%Y-%m-%d %H:%M"),
        "entendido": when.describe(dt_local, now),
        "tarea": task,
        "metodo": method,
    }


async def _llm_fallback(text: str, now: datetime) -> dict | None:
    prompt = LLM_PROMPT.format(
        hoy=f"{when.WEEKDAY_NAMES[now.weekday()]} {now:%Y-%m-%d}", hora=f"{now:%H:%M}", texto=text
    )
    try:
        raw = await get_provider().chat([{"role": "user", "content": prompt}])
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        if not data.get("fecha"):
            return None
        hora = data.get("hora") or "09:00"
        dt = datetime.strptime(f"{data['fecha']} {hora}", "%Y-%m-%d %H:%M")
        task = when._clean_task(str(data.get("tarea") or ""))
        return {"dt": dt, "task": task}
    except Exception as exc:  # salida inválida del modelo: se descarta
        log.info("El LLM no pudo interpretar la fecha (%s)", type(exc).__name__)
        return None


async def interpret(text: str) -> dict:
    """No guarda nada: devuelve lo entendido para que el usuario lo confirme."""
    now = clock.now_local()
    p = when.parse(text, now)
    if p:
        return _result(p.when, p.task, "reglas", now)
    guess = await _llm_fallback(text, now)
    if guess:
        return _result(guess["dt"], guess["task"], "llm", now)
    raise NotUnderstood(
        "No entendí la fecha. Prueba así: «recuérdame mañana a las 9 llamar a Pedro»."
    )


def _insert(conn, text: str, due_at: str, channel: str | None, chat_id: str | None,
            source_event_id: str | None = None) -> int:
    due = datetime.fromisoformat(due_at)
    if due.tzinfo is None:
        raise ValueError("due_at debe incluir zona horaria (UTC)")
    return conn.execute(
        "INSERT INTO reminders (created_at, due_at, text, channel, chat_id, source_event_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            clock.now_utc().isoformat(),
            due.astimezone(timezone.utc).isoformat(),
            text,
            channel or settings.DEFAULT_CHANNEL,
            chat_id,
            source_event_id,
        ),
    ).lastrowid


def _saved(rid: int) -> dict:
    """Relee la fila escrita: nunca se da por guardado algo que no está en la base."""
    r = get(rid)
    if not r:
        raise RuntimeError(f"el recordatorio {rid} no quedó guardado")
    log.info("Recordatorio %s · etapa 1: guardado para %s (hora local)", rid, r["due_local"])
    events.publish("reminder", {"id": rid})
    return r


def create(
    text: str,
    due_at: str,
    channel: str | None = None,
    chat_id: str | None = None,
    source_event_id: str | None = None,
) -> dict:
    with connect() as conn:
        rid = _insert(conn, text, due_at, channel, chat_id, source_event_id)
    return _saved(rid)


def create_test(channel: str | None, chat_id: str | None, seconds: int = 60) -> dict:
    """/probar_recordatorio: aviso a los N segundos, sin confirmación, para ver el camino entero."""
    due = clock.now_utc() + timedelta(seconds=seconds)
    r = create(f"Prueba de recordatorio ({seconds} s)", due.isoformat(), channel, chat_id)
    log.info("Recordatorio %s · prueba: debe llegar a %s por %s", r["id"], r["due_local"], r["channel"])
    return r


def set_pending(channel: str, chat_id: str, parsed: dict) -> dict:
    """Guarda lo entendido a la espera del "sí" (reemplaza cualquier pendiente del chat)."""
    now = clock.now_utc()
    expires = now + timedelta(minutes=settings.REMINDER_CONFIRM_MIN)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_confirmations (channel, chat_id, kind, payload,"
            " created_at, expires_at, reminder_id) VALUES (?, ?, 'reminder', ?, ?, ?, NULL)",
            (channel, chat_id, json.dumps(parsed, ensure_ascii=False), now.isoformat(), expires.isoformat()),
        )
    log.info("Confirmación pendiente en %s (caduca %s)", channel, expires.isoformat(timespec="seconds"))
    return {**parsed, "caduca": expires.isoformat(), "reminder_id": None}


def get_pending(channel: str, chat_id: str) -> dict | None:
    """Lo pendiente de ese chat (reminder_id lleno = ya confirmado); borra lo caducado."""
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE expires_at <= ?", (now,))
        r = conn.execute(
            "SELECT payload, expires_at, reminder_id FROM pending_confirmations"
            " WHERE channel = ? AND chat_id = ?",
            (channel, chat_id),
        ).fetchone()
    if not r:
        return None
    return {**json.loads(r["payload"]), "caduca": r["expires_at"], "reminder_id": r["reminder_id"]}


def clear_pending(channel: str, chat_id: str) -> bool:
    """Cancela lo que espera confirmación (lo ya confirmado no se toca)."""
    with connect() as conn:
        return conn.execute(
            "DELETE FROM pending_confirmations WHERE channel = ? AND chat_id = ?"
            " AND reminder_id IS NULL",
            (channel, chat_id),
        ).rowcount > 0


def confirm(channel: str, chat_id: str) -> dict | None:
    """Convierte lo pendiente en recordatorio, una sola vez: leer, insertar y marcar van en la
    misma transacción, y un "sí" repetido devuelve el ya creado (ya_guardado=True).
    None si no hay nada pendiente (o caducó)."""
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # dos "sí" a la vez: el segundo espera y ve reminder_id
        r = conn.execute(
            "SELECT payload, reminder_id FROM pending_confirmations"
            " WHERE channel = ? AND chat_id = ? AND expires_at > ?",
            (channel, chat_id, now),
        ).fetchone()
        if not r:
            return None
        if r["reminder_id"]:
            again = r["reminder_id"]
        else:
            again = None
            p = json.loads(r["payload"])
            rid = _insert(conn, p["tarea"], p["due_at"], channel, chat_id)
            conn.execute(
                "UPDATE pending_confirmations SET reminder_id = ? WHERE channel = ? AND chat_id = ?",
                (rid, channel, chat_id),
            )
    if again:
        saved = get(again)
        return {**saved, "ya_guardado": True} if saved else None
    return {**_saved(rid), "ya_guardado": False}


def list_pending() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pending_confirmations ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def _row(r) -> dict:
    d = dict(r)
    d["due_local"] = clock.utc_to_local(r["due_at"]).strftime("%Y-%m-%d %H:%M")
    return d


def get(rid: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    return _row(r) if r else None


def list_open(chat_id: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM reminders WHERE status IN ('pending', 'sent')"
    args: list = []
    if chat_id:
        sql += " AND chat_id = ?"
        args.append(chat_id)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY due_at LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def set_status(rid: int | None, status: str, chat_id: str | None = None) -> dict | None:
    """Marca done/cancelled. Sin id: el último recordatorio ya avisado de ese chat."""
    with connect() as conn:
        if rid is None:
            row = conn.execute(
                "SELECT id FROM reminders WHERE status = 'sent'"
                " AND (? IS NULL OR chat_id = ?) ORDER BY last_sent_at DESC LIMIT 1",
                (chat_id, chat_id),
            ).fetchone()
            if not row:
                return None
            rid = row["id"]
        n = conn.execute(
            "UPDATE reminders SET status = ?, done_at = ? WHERE id = ?"
            " AND status IN ('pending', 'sent')",
            (status, clock.now_utc().isoformat(), rid),
        ).rowcount
    return get(rid) if n else None


def due_step() -> dict:
    """Programador: pasa al buzón los avisos vencidos y las insistencias."""
    now = clock.now_utc()
    retry_before = (now - timedelta(minutes=settings.REMINDER_RETRY_MIN)).isoformat()
    sent = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status IN ('pending', 'sent') AND due_at <= ?"
            " AND attempts < ? AND (last_sent_at IS NULL OR last_sent_at <= ?)"
            " ORDER BY due_at",
            (now.isoformat(), settings.REMINDER_MAX_TRIES, retry_before),
        ).fetchall()
        for r in rows:
            n = r["attempts"] + 1
            extra = f" (aviso {n} de {settings.REMINDER_MAX_TRIES})" if n > 1 else ""
            text = (
                f"⏰ Recordatorio{extra}: {r['text']}\n"
                f"Responde /hecho {r['id']} cuando esté listo."
            )
            outbox.put(conn, r["channel"], r["chat_id"], "reminder", str(r["id"]), text)
            conn.execute(
                "UPDATE reminders SET status = 'sent', attempts = ?, last_sent_at = ? WHERE id = ?",
                (n, now.isoformat(), r["id"]),
            )
            log.info("Recordatorio %s · etapa 2: al buzón de %s (aviso %d)", r["id"], r["channel"], n)
            sent += 1
    return {"avisos": sent}
