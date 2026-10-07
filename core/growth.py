import json
from collections import Counter
from datetime import date, datetime, timedelta

from core.clock import LOCAL_TZ
from core.db import connect


def _local_day(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone(LOCAL_TZ).date()


def growth(days: int = 14) -> dict:
    today = datetime.now(LOCAL_TZ).date()
    with connect() as conn:
        events = conn.execute("SELECT created_at, kind, status FROM events").fetchall()
        chunks = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(content)), 0) AS chars"
            " FROM chunks WHERE position >= 0"
        ).fetchone()
        digests = conn.execute("SELECT concepts FROM digests WHERE status = 'done'").fetchall()

    per_day = Counter(_local_day(e["created_at"]) for e in events)
    by_kind = Counter(e["kind"] for e in events)
    pending = sum(1 for e in events if e["status"] == "pending")
    failed = sum(1 for e in events if e["status"] == "failed")

    concepts: set[str] = set()
    for d in digests:
        concepts.update(c.lower() for c in json.loads(d["concepts"] or "[]"))

    first = min(per_day) if per_day else None

    # Racha: días seguidos alimentándolo (no se rompe hasta que pase un día entero sin nada)
    cursor = today if per_day.get(today) else today - timedelta(days=1)
    streak = 0
    while per_day.get(cursor):
        streak += 1
        cursor -= timedelta(days=1)

    return {
        "dias_de_vida": (today - first).days + 1 if first else 0,
        "nacio": first.isoformat() if first else None,
        "aprendido": {
            "elementos": len(events),
            "por_tipo": dict(by_kind),
            "recuerdos": chunks["n"],
            "palabras_aprox": chunks["chars"] // 6,
            "conceptos": len(concepts),
        },
        "hoy": per_day.get(today, 0),
        "racha_dias": streak,
        "pendientes_de_digerir": pending,
        "fallidos": failed,
        "ultimos_dias": [
            {
                "dia": (today - timedelta(days=i)).isoformat(),
                "nuevos": per_day.get(today - timedelta(days=i), 0),
            }
            for i in range(days - 1, -1, -1)
        ],
    }
