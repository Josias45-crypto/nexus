"""Uso y costo del chat (OpenRouter): una fila por petición, nunca el contenido."""

from core import clock
from core.db import connect


def record(info: dict) -> None:
    now = clock.now_local()
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_usage (created_at, day, provider, model, cloud, prompt_tokens,"
            " completion_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clock.now_utc().isoformat(), now.date().isoformat(), info.get("proveedor") or "?",
                info.get("modelo"), 1, info.get("tokens_entrada") or 0,
                info.get("tokens_salida") or 0, info.get("costo_usd"),
            ),
        )


def spent_today() -> float:
    """USD gastados hoy en la nube (lo que el proveedor informó)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE day = ? AND cloud = 1",
            (clock.now_local().date().isoformat(),),
        ).fetchone()
    return float(row[0])


def by_day(days: int = 30) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT day, provider, model, cloud, COUNT(*) AS peticiones,"
            " SUM(prompt_tokens) AS tokens_entrada, SUM(completion_tokens) AS tokens_salida,"
            " SUM(cost_usd) AS costo_usd, SUM(cost_usd IS NULL) AS sin_costo"
            " FROM llm_usage GROUP BY day, provider, model, cloud ORDER BY day DESC LIMIT ?",
            (days * 10,),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["day"], {
            "dia": r["day"], "peticiones": 0, "tokens_entrada": 0, "tokens_salida": 0,
            "costo_usd": 0.0, "por_modelo": [],
        })
        d["peticiones"] += r["peticiones"]
        d["tokens_entrada"] += r["tokens_entrada"]
        d["tokens_salida"] += r["tokens_salida"]
        d["costo_usd"] = round(d["costo_usd"] + (r["costo_usd"] or 0), 6)
        d["por_modelo"].append({
            "proveedor": r["provider"], "modelo": r["model"], "nube": bool(r["cloud"]),
            "peticiones": r["peticiones"], "tokens_entrada": r["tokens_entrada"],
            "tokens_salida": r["tokens_salida"], "costo_usd": round(r["costo_usd"] or 0, 6),
            "peticiones_sin_costo": r["sin_costo"] if r["cloud"] else 0,
        })
    return list(out.values())[:days]


def alert_once(key: str, text: str) -> None:
    """Deja un aviso para el dueño en el buzón del canal por defecto, una sola vez hasta que
    clear_alert(key) lo rearme (p. ej. 402: sin saldo)."""
    from config import settings
    from core import outbox

    with connect() as conn:
        done = conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (f"alerta:{key}", clock.now_utc().isoformat()),
        ).rowcount
        if done:
            outbox.put(conn, settings.DEFAULT_CHANNEL, None, "alerta", key, text)


def clear_alert(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM kv WHERE key = ?", (f"alerta:{key}",))
