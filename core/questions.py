"""Preguntas temporales y meta, resueltas con datos de la base (no con búsqueda semántica).

- "¿qué aprendí hoy / ayer / esta semana / en los últimos 3 días?"
- "resume lo último que subí" / "¿qué fue lo último que te envié?"
- "¿qué sabes de X?" / "háblame de X"

route() devuelve None si la pregunta no es de estos tipos (sigue la búsqueda normal).
"""

import re
from datetime import date, timedelta

from core import briefing, citations, clock, when
from core.db import connect
from core.search import publish_recall, relevant, search
from providers.factory import get_provider

MAX_LIST = 15
SHORT_TEXT = 400  # más corto que esto se muestra tal cual: resumirlo invita a inventar
NEUTRAL = "Resumes textos en español con precisión. No agregues nada que no esté en el texto."
VERBS = r"(aprend|sub|guard|cont|envi|mand|entr|agreg|recib|anot|ense[nñ]|registr|ingres)\w*"
RE_ABOUT = re.compile(
    r"^\s*(?:¿\s*)?(?:qu[eé]\s+(?:sabes|tienes|recuerdas|hay)\s+(?:del?|sobre|acerca\s+del?)"
    r"|h[aá]blame\s+(?:del?|sobre)|cu[eé]ntame\s+(?:del?|sobre)|todo\s+lo\s+que\s+sabes\s+(?:del?|sobre))"
    r"\s+(.+?)\s*\??\s*$",
    re.I,
)
RE_LAST = re.compile(
    rf"\b(lo\s+[uú]ltimo|lo\s+m[aá]s\s+reciente|el\s+[uú]ltimo\s+(?:archivo|documento|audio|mensaje))\b.*\b{VERBS}"
    rf"|\b(resume|res[uú]meme|resumen\s+de)\b.*\b(lo\s+[uú]ltimo|lo\s+m[aá]s\s+reciente)",
    re.I,
)
RE_WHAT = re.compile(rf"\b(qu[eé]|cu[aá]nto|cu[aá]ntas?|cosas)\b.*\b{VERBS}|\b{VERBS}\b.*\b(qu[eé])\b", re.I)


def _range(q: str, today: date) -> tuple[date, date, str] | None:
    t = q.lower()
    if m := re.search(r"[uú]ltimos?\s+(\d{1,2})\s+d[ií]as", t):
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today, f"en los últimos {n} días"
    if re.search(r"\banteayer\b|antes\s+de\s+ayer", t):
        d = today - timedelta(days=2)
        return d, d, "anteayer"
    if re.search(r"\bayer\b", t):
        d = today - timedelta(days=1)
        return d, d, "ayer"
    if re.search(r"\bhoy\b", t):
        return today, today, "hoy"
    if re.search(r"semana\s+pasada", t):
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), "la semana pasada"
    if re.search(r"\b(esta|la)\s+semana\b", t):
        return today - timedelta(days=today.weekday()), today, "esta semana"
    if re.search(r"\beste\s+mes\b", t):
        return today.replace(day=1), today, "este mes"
    for name, wd in when.WEEKDAYS.items():
        if re.search(rf"\bel\s+{name}\b", t):
            d = today - timedelta(days=(today.weekday() - wd) % 7 or 7)
            return d, d, f"el {when.WEEKDAY_NAMES[wd]} {d.day}"
    return None


def _source(e: dict, n: int) -> dict:
    return {
        "n": n, "event_id": e["id"], "filename": e["archivo"], "cita": e["archivo"],
        "ubicacion": None, "position": 0, "distance": None,
        "snippet": "(privado)" if e["privado"] else e["resumen"][:200],
    }


def _temporal(start: date, end: date, label: str) -> dict:
    items = briefing.learned(start, limit=200, until_day=end)
    if not items:
        return {"answer": f"No aprendí nada {label}.", "sources": [], "llm": None, "tipo": "temporal"}
    multi_day = start != end
    lines = [f"{label[0].upper()}{label[1:]} aprendí {len(items)} cosa(s):"]
    for e in items[-MAX_LIST:]:
        when_ = f"{e['dia'][8:10]}/{e['dia'][5:7]} {e['hora']}" if multi_day else e["hora"]
        detalle = "(privado)" if e["privado"] else (e["resumen"] or e["estado"])
        lines.append(f"• {when_} {e['archivo']}: {detalle}")
    if len(items) > MAX_LIST:
        lines.append(f"… y {len(items) - MAX_LIST} más antes.")
    sources = [_source(e, i) for i, e in enumerate(items[-MAX_LIST:], 1)]
    return {"answer": "\n".join(lines), "sources": sources, "llm": None, "tipo": "temporal"}


async def _last() -> dict:
    with connect() as conn:
        e = conn.execute(
            "SELECT e.id, e.created_at, e.filename, e.status, e.private, d.summary"
            " FROM events e LEFT JOIN digests d ON d.event_id = e.id AND d.status = 'done'"
            " ORDER BY e.created_at DESC LIMIT 1"
        ).fetchone()
        if not e:
            return {"answer": "Todavía no me has enviado nada.", "sources": [], "llm": None, "tipo": "ultimo"}
        parts = [
            r["content"]
            for r in conn.execute(
                "SELECT content FROM chunks WHERE event_id = ? AND position >= 0 ORDER BY position LIMIT 6",
                (e["id"],),
            )
        ]
    local = clock.utc_to_local(e["created_at"])
    head = f"Lo último que me enviaste fue «{e['filename']}» ({local:%d/%m %H:%M})."
    item = {"id": e["id"], "archivo": e["filename"], "privado": bool(e["private"]), "resumen": e["summary"] or ""}
    if e["status"] == "pending":
        return {"answer": f"{head} Todavía lo estoy procesando.", "sources": [], "llm": None, "tipo": "ultimo"}
    if e["summary"]:
        return {"answer": f"{head}\n\n{e['summary']}", "sources": [_source(item, 1)], "llm": None, "tipo": "ultimo"}
    if not parts:
        return {"answer": f"{head} No encontré texto en él ({e['status']}).", "sources": [], "llm": None, "tipo": "ultimo"}
    text = "\n\n".join(parts)[:6000]
    if len(text) <= SHORT_TEXT:
        item["resumen"] = text
        return {"answer": f"{head}\n\n{text}", "sources": [_source(item, 1)], "llm": None, "tipo": "ultimo"}
    summary, llm = await get_provider().chat_ex(
        [
            {"role": "system", "content": NEUTRAL},
            {"role": "user", "content": f"Resume en 2 o 3 frases este texto, sin inventar nada:\n\n{text}"},
        ],
        cloud=True,
        private=bool(e["private"]),
    )
    item["resumen"] = summary
    return {"answer": f"{head}\n\n{summary.strip()}", "sources": [_source(item, 1)], "llm": llm, "tipo": "ultimo"}


async def _about(topic: str) -> dict:
    from core.ask import NO_INFO, sources_for

    hits = relevant(await search(topic, 8, publish=False))
    if not hits:
        return {"answer": NO_INFO, "sources": [], "llm": None, "tipo": "sobre"}
    publish_recall("ask", hits)
    context = "\n\n".join(f"Fuente {i} ({citations.cite(h)}):\n{h['content']}" for i, h in enumerate(hits, 1))
    answer, llm = await get_provider().chat_ex(
        [
            {"role": "system", "content": f"{NEUTRAL} Usa solo la información de las fuentes."},
            {"role": "user", "content": f"{context}\n\nResume en 3 a 5 frases todo lo que dicen las fuentes sobre: {topic}"},
        ],
        cloud=True,
        private=any(h.get("private") for h in hits),
    )
    answer = citations.strip_markers(answer)
    return {"answer": answer, "sources": sources_for(citations.supporting(answer, hits)), "llm": llm, "tipo": "sobre"}


async def route(question: str) -> dict | None:
    if m := RE_ABOUT.match(question):
        return await _about(m.group(1).strip(" ?¿.\"'"))
    if RE_LAST.search(question):
        return await _last()
    if RE_WHAT.search(question):
        rng = _range(question, clock.now_local().date())
        if rng:
            return _temporal(*rng)
    return None
