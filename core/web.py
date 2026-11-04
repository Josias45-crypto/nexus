"""La web (Fase 5) es otra ventana de la misma conversación de Telegram: lo que el jefe escribe
aquí usa el mismo chat (historial, preguntas pendientes, recordatorios y saludos, que siguen
llegando por Telegram). Más el panel esencial: días de vida, elementos y gasto de hoy (sin racha).
"""

import os
from datetime import date

from core import charla, clock, iniciativa, usage
from core.db import connect

CANAL = "web"


def conversacion(channel: str, chat_id: str) -> tuple[str, str]:
    """El canal y chat donde vive la conversación. Para la web, el de Telegram del jefe."""
    if channel != CANAL:
        return channel, chat_id
    d = iniciativa._destino()
    if d and d.get("channel") and d.get("channel") != CANAL and d.get("chat_id"):
        return d["channel"], str(d["chat_id"])
    ids = [x.strip() for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip()]
    return ("telegram", ids[0]) if ids else (CANAL, CANAL)


def historial(n: int = 40) -> list[dict]:
    _, chat = conversacion(CANAL, CANAL)
    return [{"rol": m["rol"], "texto": m["texto"], "hora": clock.utc_to_local(m["created_at"]).strftime("%Y-%m-%d %H:%M")}
            for m in charla.recientes(n, chat)]


def panel() -> dict:
    with connect() as conn:
        primero = conn.execute("SELECT MIN(created_at) FROM events").fetchone()[0]
        elementos = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    hoy = clock.now_local().date()
    dias = (hoy - clock.utc_to_local(primero).date()).days + 1 if primero else 0
    return {"dias_de_vida": dias, "elementos": elementos, "gasto_hoy_usd": round(usage.spent_today(), 4),
            "desde": clock.utc_to_local(primero).date().isoformat() if primero else date.today().isoformat()}
