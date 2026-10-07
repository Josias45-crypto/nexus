import re

import sqlite_vec

from core import events
from core.db import connect
from providers.embeddings import get_embedder

RRF_K = 60  # constante estándar de Reciprocal Rank Fusion


def _fts_query(query: str) -> str:
    words = [w for w in re.findall(r"\w+", query) if len(w) >= 4]
    return " OR ".join(f'"{w}"' for w in words)


async def search(query: str, k: int = 5, publish: bool = True) -> list[dict]:
    qvec = await get_embedder().embed_query(query)
    pool = k * 4
    fts = _fts_query(query)

    with connect() as conn:
        vec_rows = conn.execute(
            "SELECT rowid, distance FROM vec_chunks"
            " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(qvec), pool),
        ).fetchall()
        fts_rows = (
            conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts, pool),
            ).fetchall()
            if fts
            else []
        )

        distances = {r["rowid"]: r["distance"] for r in vec_rows}
        scores: dict[int, float] = {}
        for ranking in (vec_rows, fts_rows):
            for rank, r in enumerate(ranking):
                scores[r["rowid"]] = scores.get(r["rowid"], 0.0) + 1.0 / (RRF_K + rank + 1)

        results = []
        for cid in sorted(scores, key=scores.get, reverse=True)[:k]:
            row = conn.execute(
                "SELECT c.id, c.position, c.content, e.id AS event_id, e.filename,"
                " e.source, e.created_at, e.private"
                " FROM chunks c JOIN events e ON e.id = c.event_id WHERE c.id = ?",
                (cid,),
            ).fetchone()
            if row:
                d = distances.get(cid)
                results.append(
                    {
                        **dict(row),
                        "score": round(scores[cid], 5),
                        "distance": round(d, 4) if d is not None else None,
                    }
                )
    if publish:
        publish_recall("search", results)
    return results


def publish_recall(origen: str, hits: list[dict]) -> None:
    """Avisa a la vista en vivo qué recuerdos se usaron (solo ids, en orden de relevancia)."""
    ids = list(dict.fromkeys(h["event_id"] for h in hits))
    if ids:
        events.publish("recall", {"origen": origen, "ids": ids})
