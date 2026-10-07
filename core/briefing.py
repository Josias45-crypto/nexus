"""Lo de hoy (/today) y resumen matutino proactivo.

Los datos se arman con código (recordatorios, lo último que entró); el LLM solo redacta un
saludo breve a partir de esos datos. La lista de datos va siempre tal cual, así nada inventado
puede colarse como dato. El contenido marcado como privado nunca se pasa al LLM.
"""

import logging
import re
from datetime import date, datetime, timedelta

from config import settings
from core import clock, outbox, profile
from core.db import connect
from providers.factory import get_provider

log = logging.getLogger("nexus.briefing")

SNIPPET = 160
LATE_WINDOW_HOURS = 3  # si NEXUS estuvo apagado, el resumen se envía hasta 3 h tarde, no más
KV_LAST = "briefing_last_day"


def _day_bounds_utc(day: date) -> tuple[str, str]:
    start = clock.local_to_utc(datetime.combine(day, datetime.min.time()))
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _snippet(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SNIPPET else text[: SNIPPET - 1] + "…"


def learned(day: date | None = None, since_utc: str | None = None, limit: int = 30) -> list[dict]:
    """Eventos de un día local (o desde un instante UTC) con su resumen o primer trozo."""
    if since_utc:
        start, end = since_utc, clock.now_utc().isoformat()
    else:
        start, end = _day_bounds_utc(day or clock.now_local().date())
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.created_at, e.kind, e.filename, e.status, e.private, d.summary,"
            " (SELECT content FROM chunks c WHERE c.event_id = e.id AND c.position = 0) AS first"
            " FROM events e LEFT JOIN digests d ON d.event_id = e.id AND d.status = 'done'"
            " WHERE e.created_at >= ? AND e.created_at < ? ORDER BY e.created_at DESC LIMIT ?",
            (start, end, limit),
        ).fetchall()[::-1]  # los más recientes, en orden cronológico
    return [
        {
            "id": r["id"],
            "hora": clock.utc_to_local(r["created_at"]).strftime("%H:%M"),
            "tipo": r["kind"],
            "archivo": r["filename"],
            "estado": r["status"],
            "privado": bool(r["private"]),
            "resumen": _snippet(r["summary"] or r["first"]),
        }
        for r in rows
    ]


def reminders_for(day: date) -> dict:
    start, end = _day_bounds_utc(day)
    with connect() as conn:
        today = conn.execute(
            "SELECT id, due_at, text FROM reminders WHERE status IN ('pending', 'sent')"
            " AND due_at >= ? AND due_at < ? ORDER BY due_at",
            (start, end),
        ).fetchall()
        overdue = conn.execute(
            "SELECT id, due_at, text FROM reminders WHERE status IN ('pending', 'sent')"
            " AND due_at < ? ORDER BY due_at",
            (start,),
        ).fetchall()

    def fmt(r, with_day=False):
        local = clock.utc_to_local(r["due_at"])
        return {"id": r["id"], "cuando": local.strftime("%d/%m %H:%M" if with_day else "%H:%M"), "texto": r["text"]}

    return {"hoy": [fmt(r) for r in today], "atrasados": [fmt(r, True) for r in overdue]}


def today(day: date | None = None) -> dict:
    day = day or clock.now_local().date()
    items = learned(day)
    return {"dia": day.isoformat(), "total": len(items), "elementos": items, "recordatorios": reminders_for(day)}


def _facts_text(rem: dict, items: list[dict]) -> str:
    lines: list[str] = []
    if rem["hoy"]:
        lines.append("Recordatorios de hoy:")
        lines += [f"• {r['cuando']} — {r['texto']} (/hecho {r['id']})" for r in rem["hoy"]]
    if rem["atrasados"]:
        lines.append("Pendientes atrasados:")
        lines += [f"• {r['cuando']} — {r['texto']} (/hecho {r['id']})" for r in rem["atrasados"]]
    if items:
        lines.append("Lo último que aprendí:")
        for it in items[-8:]:
            detalle = "(privado)" if it["privado"] else it["resumen"]
            lines.append(f"• {it['archivo']}: {detalle}" if detalle else f"• {it['archivo']}")
    return "\n".join(lines)


def _compact(rem: dict, items: list[dict]) -> str:
    """Lo mínimo para el saludo: cuántas cosas hay y qué recordatorios (sin contenido privado)."""
    parts = [f"Recordatorios de hoy: {len(rem['hoy'])}"]
    parts += [f"- {r['cuando']} {r['texto']}" for r in rem["hoy"][:3]]
    parts.append(f"Pendientes atrasados: {len(rem['atrasados'])}")
    parts.append(f"Elementos nuevos desde ayer: {len(items)}")
    return "\n".join(parts)


def _valid_greeting(reply: str, compact: str) -> str | None:
    """Primera frase, corta y sin números que no estén en los datos."""
    first = re.split(r"(?<=[.!?])\s", " ".join(reply.split()), maxsplit=1)[0].strip()
    if len(first.split()) < 6 or len(first) > 220 or re.match(r"^[\-\*\d•]", first):
        return None
    if any(n not in compact for n in re.findall(r"\d+", first)):
        return None
    return first


async def _greeting(rem: dict, items: list[dict]) -> str:
    """Saludo de una frase redactado por el LLM y validado; si no sirve, uno fijo."""
    p = profile.get()
    fallback = f"Buenos días. Soy {p['asistente']['nombre']}; esto es lo que tienes hoy."
    compact = _compact(rem, items)
    try:
        reply = await get_provider().chat(
            [
                {"role": "system", "content": profile.system_prompt()},
                {
                    "role": "user",
                    "content": "Escribe UNA sola frase de buenos días para tu dueño que diga lo "
                    "más importante de estos datos. No inventes nada, no uses listas ni "
                    f"exclamaciones.\n\n{compact}",
                },
            ]
        )
    except Exception as exc:
        log.warning("Saludo con LLM no disponible (%s); uso el fijo", type(exc).__name__)
        return fallback
    return _valid_greeting(reply, compact) or fallback


async def build(day: date | None = None) -> dict:
    day = day or clock.now_local().date()
    since = (clock.now_utc() - timedelta(hours=24)).isoformat()
    rem = reminders_for(day)
    items = learned(since_utc=since)
    facts = _facts_text(rem, items)
    if not facts:
        text = "Buenos días. Hoy no tienes recordatorios y en las últimas 24 h no entró nada nuevo."
    else:
        text = f"{await _greeting(rem, items)}\n\n{facts}"
    return {"dia": day.isoformat(), "texto": text, "recordatorios": rem, "nuevos": len(items)}


def _scheduled_time() -> datetime | None:
    hora = profile.get()["proactivo"]["resumen_matutino"]
    if not hora:
        return None
    h, m = map(int, hora.split(":"))
    return datetime.combine(clock.now_local().date(), datetime.min.time()).replace(hour=h, minute=m)


async def send(channel: str | None = None) -> dict:
    data = await build()
    with connect() as conn:
        mid = outbox.put(conn, channel or settings.DEFAULT_CHANNEL, None, "briefing", data["dia"], data["texto"])
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (KV_LAST, data["dia"]),
        )
    return {"outbox_id": mid, **data}


async def due_step() -> dict:
    """Programador: envía el resumen una vez al día, desde la hora del perfil."""
    at = _scheduled_time()
    now = clock.now_local()
    if not at or not (at <= now < at + timedelta(hours=LATE_WINDOW_HOURS)):
        return {}
    with connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (KV_LAST,)).fetchone()
    if row and row["value"] == now.date().isoformat():
        return {}
    result = await send()
    log.info("Resumen matutino en el buzón (%d nuevos)", result["nuevos"])
    return {"resumen_matutino": result["dia"]}
