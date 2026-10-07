"""Recordatorios: interpretar, confirmar, guardar y avisar con insistencia.

Flujo: el canal llama a interpret() y muestra la fecha entendida; solo si el usuario confirma,
llama a create(). El programador (worker) deja los avisos vencidos en el buzón de salida
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


def create(
    text: str,
    due_at: str,
    channel: str | None = None,
    chat_id: str | None = None,
    source_event_id: str | None = None,
) -> dict:
    due = datetime.fromisoformat(due_at)
    if due.tzinfo is None:
        raise ValueError("due_at debe incluir zona horaria (UTC)")
    with connect() as conn:
        rid = conn.execute(
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
    events.publish("reminder", {"id": rid})
    return get(rid)


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
            sent += 1
    return {"avisos": sent}
