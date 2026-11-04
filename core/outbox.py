"""Buzón de salida: mensajes que Isabelle quiere enviar. Cada canal los recoge con GET /outbox y
confirma con POST /outbox/{id}/delivered (así el núcleo no conoce tokens).

Un mensaje cuenta como entregado SOLO con el acuse de la plataforma (el message_id de Telegram). Si
el envío falla (429, 400, chat bloqueado, sin red), el canal lo informa con POST /outbox/{id}/fallo
y se reintenta con espera creciente (15 s, 30 s, 1 min… hasta 10 min, o el retry_after de un 429).
"""

import json
import logging
from datetime import datetime, timedelta

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
            "SELECT id, created_at, chat_id, kind, ref_id, text, botones, intentos FROM outbox"
            " WHERE channel = ? AND delivered_at IS NULL AND (proximo_intento IS NULL OR proximo_intento <= ?)"
            " ORDER BY id LIMIT ?",
            (channel, now.isoformat(), limit),
        ).fetchall()
    return [{**dict(r), "botones": json.loads(r["botones"]) if r["botones"] else None} for r in rows]


REINTENTO_BASE_S, REINTENTO_MAX_S = 15, 600


def delivered(msg_id: int, acuse: str) -> bool:
    """Entregado con el acuse de la plataforma (message_id). Sin acuse no cuenta."""
    if not str(acuse or "").strip():
        return False
    with connect() as conn:
        ok = conn.execute(
            "UPDATE outbox SET delivered_at = ?, acuse = ?, proximo_intento = NULL WHERE id = ? AND delivered_at IS NULL",
            (clock.now_utc().isoformat(), str(acuse)[:200], msg_id),
        ).rowcount == 1
        r = conn.execute("SELECT kind, ref_id, channel, created_at FROM outbox WHERE id = ?", (msg_id,)).fetchone()
    if ok and r and r["kind"] == "reminder":
        demora = (clock.now_utc() - datetime.fromisoformat(r["created_at"])).total_seconds()
        log.info("Recordatorio %s · etapa 3: entregado por %s (acuse %s, %.0f s después de salir al buzón)",
                 r["ref_id"], r["channel"], acuse, demora)
    return ok


def fallo(msg_id: int, error: str, reintentar_en: float | None = None) -> dict | None:
    """El canal no pudo entregarlo: se reintenta con espera creciente (o la que pidió la plataforma)."""
    now = clock.now_utc()
    with connect() as conn:
        r = conn.execute("SELECT intentos, kind, ref_id FROM outbox WHERE id = ? AND delivered_at IS NULL", (msg_id,)).fetchone()
        if not r:
            return None
        n = r["intentos"] + 1
        espera = reintentar_en if reintentar_en else min(REINTENTO_MAX_S, REINTENTO_BASE_S * 2 ** (n - 1))
        conn.execute("UPDATE outbox SET intentos = ?, proximo_intento = ?, ultimo_error = ? WHERE id = ?",
                     (n, (now + timedelta(seconds=espera)).isoformat(), str(error)[:200], msg_id))
    log.warning("Buzón %s (%s %s): no se pudo entregar (%s), intento %d; reintento en %.0f s",
                msg_id, r["kind"], r["ref_id"], error, n, espera)
    return {"intentos": n, "reintento_s": espera}


def estado() -> dict:
    """Para /health: lo que lleva más de 2 min sin entregarse y los errores de envío recientes."""
    hace = (clock.now_utc() - timedelta(minutes=2)).isoformat()
    with connect() as conn:
        sin_entregar = conn.execute("SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL AND kind = 'reminder' AND created_at < ?",
                                    (hace,)).fetchone()[0]
        con_error = conn.execute("SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL AND intentos > 0").fetchone()[0]
    return {"recordatorios_sin_entregar": sin_entregar, "pendientes_con_error": con_error}
