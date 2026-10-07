import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import sqlite_vec

from config import settings
from core import events
from core.db import connect
from providers.embeddings import get_embedder
from providers.factory import get_provider

log = logging.getLogger("nexus.digest")

SECTION_CHARS = 6000  # cabe en el contexto de un modelo pequeño
_lock = asyncio.Lock()

SYSTEM = (
    "Eres un asistente que resume textos en español con precisión. "
    "No inventes datos que no estén en el texto."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _llm(prompt: str) -> str:
    reply = await get_provider().chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    )
    return reply.strip()


def _group(parts: list[str], limit: int) -> list[str]:
    groups: list[str] = []
    current = ""
    for p in parts:
        if current and len(current) + 2 + len(p) > limit:
            groups.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        groups.append(current)
    return groups


async def summarize(parts: list[str]) -> str:
    """Resume por secciones y luego combina los resúmenes parciales."""
    sections = _group(parts, SECTION_CHARS)
    summaries = [
        await _llm(f"Resume en 3 o 4 frases el siguiente texto:\n\n{s}") for s in sections
    ]
    while len(summaries) > 1:
        merged = _group(summaries, SECTION_CHARS)
        if len(merged) >= len(summaries):
            merged = ["\n\n".join(summaries)[:SECTION_CHARS]]
        summaries = [
            await _llm(
                "Combina estos resúmenes parciales en un solo resumen coherente "
                f"de 4 a 6 frases:\n\n{m}"
            )
            for m in merged
        ]
    return summaries[0]


async def extract_concepts(summary: str) -> list[str]:
    raw = await _llm(
        "Del siguiente resumen, lista entre 5 y 8 conceptos clave. "
        "Responde solo con los conceptos separados por comas, sin explicaciones:\n\n"
        + summary
    )
    concepts: list[str] = []
    for item in re.split(r"[,;\n]", raw):
        item = re.sub(r"^[\s\-\*\d\.\)]+", "", item).strip(" .\"'")
        if 2 <= len(item) <= 60 and item.lower() not in {c.lower() for c in concepts}:
            concepts.append(item)
    return concepts[:8]


def _mark(event_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO digests (event_id, status, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT(event_id) DO UPDATE SET status = excluded.status",
            (event_id, status, _now()),
        )


def _register_failure(event_id: str, error: str) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM digests WHERE event_id = ?", (event_id,)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        status = "failed" if attempts >= settings.MAX_ATTEMPTS else "retry"
        conn.execute(
            "INSERT INTO digests (event_id, status, attempts, error, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(event_id) DO UPDATE SET status = excluded.status,"
            " attempts = excluded.attempts, error = excluded.error",
            (event_id, status, attempts, error[:500], _now()),
        )
    return status


async def _digest_event(event_id: str) -> str:
    with connect() as conn:
        parts = [
            r["content"]
            for r in conn.execute(
                "SELECT content FROM chunks WHERE event_id = ? AND position >= 0"
                " ORDER BY position",
                (event_id,),
            )
        ]
    if sum(len(p) for p in parts) < settings.DIGEST_MIN_CHARS:
        _mark(event_id, "skipped")
        return "skipped"

    summary = await summarize(parts)
    concepts = await extract_concepts(summary)
    text = summary + (f"\nConceptos clave: {', '.join(concepts)}" if concepts else "")
    vec = (await get_embedder().embed_documents([text]))[0]

    # El resumen entra a la memoria como un recuerdo más (position = -1)
    with connect() as conn:
        cid = conn.execute(
            "INSERT INTO chunks (event_id, position, content) VALUES (?, -1, ?)",
            (event_id, text),
        ).lastrowid
        conn.execute("INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (cid, text))
        conn.execute(
            "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
            (cid, sqlite_vec.serialize_float32(vec)),
        )
        conn.execute(
            "INSERT INTO digests (event_id, status, summary, concepts, model, created_at)"
            " VALUES (?, 'done', ?, ?, ?, ?)"
            " ON CONFLICT(event_id) DO UPDATE SET status = 'done',"
            " summary = excluded.summary, concepts = excluded.concepts,"
            " model = excluded.model, error = NULL, created_at = excluded.created_at",
            (
                event_id,
                summary,
                json.dumps(concepts, ensure_ascii=False),
                settings.LLM_MODEL,
                _now(),
            ),
        )
    events.publish("digested", {"id": event_id, "conceptos": concepts})
    return "done"


async def digest_pending(limit: int = 5) -> dict:
    async with _lock:
        with connect() as conn:
            rows = conn.execute(
                "SELECT e.id FROM events e LEFT JOIN digests d ON d.event_id = e.id"
                " WHERE e.status = 'processed' AND e.kind IN ('text', 'document', 'audio')"
                " AND (d.event_id IS NULL OR d.status = 'retry')"
                " ORDER BY e.created_at LIMIT ?",
                (limit,),
            ).fetchall()

        result = {"digeridos": 0, "omitidos": 0, "a_reintentar": 0, "fallidos": 0}
        for row in rows:
            try:
                outcome = await _digest_event(row["id"])
                result["digeridos" if outcome == "done" else "omitidos"] += 1
            except Exception as exc:
                log.exception("Fallo digiriendo %s", row["id"])
                status = _register_failure(row["id"], f"{type(exc).__name__}: {exc}")
                result["fallidos" if status == "failed" else "a_reintentar"] += 1
        return result
