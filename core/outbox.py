"""Buzón de salida: mensajes que NEXUS quiere enviar. Cada canal los recoge con
GET /outbox y confirma con POST /outbox/{id}/delivered (así el núcleo no conoce tokens)."""

import json
import logging
from datetime import timedelta

from core import clock
from core.db import connect

log = logging.getLogger("nexus.outbox")

# Un resumen matutino que nadie recogió a tiempo (canal apagado) ya no tiene sentido
STALE = {"briefing": timedelta(hours=12), "saludo": timedelta(hours=3)}


def put(conn, channel: str, chat_id: str | None, kind: str, ref_id: str | None, text: str,
        botones: list | None = None) -> int:
    """Se llama dentro de la transacción del que genera el mensaje. `botones`: filas de
    {"texto", "dato"} que el canal muestra dentro del chat."""
    return conn.execute(
        "INSERT INTO outbox (created_at, channel, chat_id, kind, ref_id, text, botones)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (clock.now_utc().isoformat(), channel, chat_id, kind, ref_id, text,
         json.dumps(botones, ensure_ascii=False) if botones else None),
    ).lastrowid


def pending(channel: str, limit: int = 20) -> list[dict]:
    with connect() as conn:
        now = clock.now_utc()
        for kind, age in STALE.items():
            conn.execute(
                "UPDATE outbox SET delivered_at = ? WHERE channel = ? AND kind = ?"
                " AND delivered_at IS NULL AND created_at < ?",
                (f"{now.isoformat()} (vencido)", channel, kind, (now - age).isoformat()),
            )
        rows = conn.execute(
            "SELECT id, created_at, chat_id, kind, ref_id, text, botones FROM outbox"
            " WHERE channel = ? AND delivered_at IS NULL ORDER BY id LIMIT ?",
            (channel, limit),
        ).fetchall()
    return [{**dict(r), "botones": json.loads(r["botones"]) if r["botones"] else None} for r in rows]


def delivered(msg_id: int) -> bool:
    with connect() as conn:
        ok = conn.execute(
            "UPDATE outbox SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL",
            (clock.now_utc().isoformat(), msg_id),
        ).rowcount == 1
        r = conn.execute("SELECT kind, ref_id, channel FROM outbox WHERE id = ?", (msg_id,)).fetchone()
    if ok and r and r["kind"] == "reminder":
        log.info("Recordatorio %s · etapa 3: entregado por %s", r["ref_id"], r["channel"])
    return ok
