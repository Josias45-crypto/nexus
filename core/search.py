import re

from config import settings

import sqlite_vec

from core import events
from core.db import connect
from core.memory import parse_meta
from providers.embeddings import get_embedder

RRF_K = 60  # constante estándar de Reciprocal Rank Fusion


def _fts_query(query: str) -> str:
    words = [w for w in re.findall(r"\w+", query) if len(w) >= 4]
    return " OR ".join(f'"{w}"' for w in words)


# Palabras con mayúscula que no son nombres propios (inicio de pregunta)
_NOT_NAMES = {
    "que", "qué", "cual", "cuál", "cuales", "cuáles", "cuanto", "cuánto", "cuanta", "cuánta",
    "cuantos", "cuántos", "cuantas", "cuántas", "como", "cómo", "donde", "dónde", "cuando",
    "cuándo", "quien", "quién", "quienes", "quiénes", "por", "para", "el", "la", "los", "las",
    "un", "una", "dime", "dame", "hay", "tengo", "tiene", "me", "en", "de", "a", "y", "o",
}


def rare_terms(query: str) -> list[str]:
    """Candidatos a término raro: nombres propios (con mayúscula) y cifras o códigos."""
    out = []
    for w in re.findall(r"\w+", query):
        if any(c.isdigit() for c in w) or (w[0].isupper() and w.lower() not in _NOT_NAMES and len(w) >= 3):
            if w.lower() not in (x.lower() for x in out):
                out.append(w)
    return out


def _rare_rows(conn, query: str) -> set[int]:
    """Trozos que contienen un término raro de la pregunta (aparece en pocos trozos)."""
    ids: set[int] = set()
    limit = settings.RARE_MAX_DF
    for term in rare_terms(query):
        rows = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
            (f'"{term}"', limit + 1),
        ).fetchall()
        if 0 < len(rows) <= limit:
            ids.update(r["rowid"] for r in rows)
    return ids


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
        fts_ids = {r["rowid"] for r in fts_rows}
        rare_ids = _rare_rows(conn, query)
        fts_rows = list(fts_rows) + [{"rowid": i} for i in rare_ids - fts_ids]
        fts_ids |= rare_ids
        # Los que solo encontró la búsqueda por palabras también reciben su distancia real,
        # para que el umbral NEXUS_MAX_DISTANCE se aplique a todos por igual
        qblob = sqlite_vec.serialize_float32(qvec)
        for r in fts_rows:
            if r["rowid"] not in distances:
                row = conn.execute(
                    "SELECT vec_distance_l2(embedding, ?) AS d FROM vec_chunks WHERE rowid = ?",
                    (qblob, r["rowid"]),
                ).fetchone()
                if row:
                    distances[r["rowid"]] = row["d"]
        scores: dict[int, float] = {}
        for ranking in (vec_rows, fts_rows):
            for rank, r in enumerate(ranking):
                scores[r["rowid"]] = scores.get(r["rowid"], 0.0) + 1.0 / (RRF_K + rank + 1)

        ranked = sorted(scores, key=scores.get, reverse=True)
        # Un término raro (nombre propio, cifra) entra siempre, aunque no esté en el top k
        chosen = ranked[:k] + [c for c in ranked[k:] if c in rare_ids]
        results = []
        for cid in chosen:
            row = conn.execute(
                "SELECT c.id, c.position, c.content, c.meta, e.id AS event_id, e.filename,"
                " e.source, e.created_at, e.private"
                " FROM chunks c JOIN events e ON e.id = c.event_id WHERE c.id = ?",
                (cid,),
            ).fetchone()
            if row:
                d = distances.get(cid)
                results.append(
                    {
                        **dict(row),
                        "meta": parse_meta(row["meta"]),
                        "coincide_texto": cid in fts_ids,
                        "termino_raro": cid in rare_ids,
                        "score": round(scores[cid], 5),
                        "distance": round(d, 4) if d is not None else None,
                    }
                )
    if publish:
        publish_recall("search", results)
    return results


def relevant(hits: list[dict], max_distance: float | None = None, margin: float | None = None) -> list[dict]:
    """Filtra por umbral: distancia <= T, o <= T + margen si también coincide por palabras."""
    t = settings.MAX_DISTANCE if max_distance is None else max_distance
    m = settings.FTS_MARGIN if margin is None else margin
    return [
        h for h in hits
        if h["distance"] is not None
        and (h["distance"] <= t or (h.get("coincide_texto") and h["distance"] <= t + m))
    ]


def publish_recall(origen: str, hits: list[dict]) -> None:
    """Avisa a la vista en vivo qué recuerdos se usaron (solo ids, en orden de relevancia)."""
    ids = list(dict.fromkeys(h["event_id"] for h in hits))
    if ids:
        events.publish("recall", {"origen": origen, "ids": ids})
