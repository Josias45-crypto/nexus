"""Preguntas pendientes con caducidad, en la base (pending_confirmations, una por chat).

Mismo patrón que la hora ambigua: Isabelle pregunta, guarda lo que espera con su caducidad y el
siguiente mensaje del jefe la responde o la descarta (si no la responde, se atiende normal: nunca
se traga un mensaje). Tipos: 'web' (¿lo guardo?), 'busqueda' (búsqueda anunciada) y 'olvido'.
"""

import json
from datetime import timedelta

from core import clock
from core.db import connect


def guardar(channel: str, chat_id: str, kind: str, datos: dict, minutos: float) -> None:
    now = clock.now_utc()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_confirmations (channel, chat_id, kind, payload, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (channel, chat_id, kind, json.dumps(datos, ensure_ascii=False), now.isoformat(),
             (now + timedelta(minutes=minutos)).isoformat()),
        )


def tomar(channel: str, chat_id: str, kind: str) -> dict | None:
    """Lo pendiente de ese tipo (y lo borra): None si no hay o caducó."""
    with connect() as conn:
        r = conn.execute(
            "SELECT payload, expires_at FROM pending_confirmations WHERE channel = ? AND chat_id = ? AND kind = ?",
            (channel, chat_id, kind),
        ).fetchone()
        if not r:
            return None
        conn.execute("DELETE FROM pending_confirmations WHERE channel = ? AND chat_id = ? AND kind = ?",
                     (channel, chat_id, kind))
    if r["expires_at"] <= clock.now_utc().isoformat():
        return None
    return json.loads(r["payload"])


def hay(channel: str, chat_id: str, kind: str) -> bool:
    with connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM pending_confirmations WHERE channel = ? AND chat_id = ? AND kind = ? AND expires_at > ?",
            (channel, chat_id, kind, clock.now_utc().isoformat()),
        ).fetchone()
    return bool(r)
