"""Buzón de salida: mensajes que NEXUS quiere enviar. Cada canal los recoge con
GET /outbox y confirma con POST /outbox/{id}/delivered (así el núcleo no conoce tokens)."""

from core import clock
from core.db import connect


def put(conn, channel: str, chat_id: str | None, kind: str, ref_id: str | None, text: str) -> int:
    """Se llama dentro de la transacción del que genera el mensaje."""
    return conn.execute(
        "INSERT INTO outbox (created_at, channel, chat_id, kind, ref_id, text)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (clock.now_utc().isoformat(), channel, chat_id, kind, ref_id, text),
    ).lastrowid


def pending(channel: str, limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, chat_id, kind, ref_id, text FROM outbox"
            " WHERE channel = ? AND delivered_at IS NULL ORDER BY id LIMIT ?",
            (channel, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delivered(msg_id: int) -> bool:
    with connect() as conn:
        return (
            conn.execute(
                "UPDATE outbox SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL",
                (clock.now_utc().isoformat(), msg_id),
            ).rowcount
            == 1
        )
