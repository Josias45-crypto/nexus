import logging
from pathlib import Path

import sqlite_vec

from core.chunker import chunk_text
from core.db import connect
from providers.embeddings import get_embedder

log = logging.getLogger("nexus.processor")

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}


def extract_text(path: Path, mime: str) -> str | None:
    ext = path.suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if ext in TEXT_EXT or mime.startswith("text/"):
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def _set_status(event_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))


async def process_pending(limit: int = 10) -> dict:
    embedder = get_embedder()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, raw_path, mime FROM events"
            " WHERE status = 'pending' AND kind IN ('text', 'document')"
            " ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()

    result = {"procesados": 0, "sin_texto": 0, "no_soportados": 0, "fallidos": 0, "trozos": 0}
    for row in rows:
        try:
            text = extract_text(Path(row["raw_path"]), row["mime"] or "")
            if text is None:
                _set_status(row["id"], "unsupported")
                result["no_soportados"] += 1
                continue
            chunks = chunk_text(text)
            if not chunks:
                _set_status(row["id"], "empty")
                result["sin_texto"] += 1
                continue

            vectors = await embedder.embed_documents(chunks)
            with connect() as conn:
                for pos, (content, vec) in enumerate(zip(chunks, vectors)):
                    cid = conn.execute(
                        "INSERT INTO chunks (event_id, position, content) VALUES (?, ?, ?)",
                        (row["id"], pos, content),
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (cid, content)
                    )
                    conn.execute(
                        "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                        (cid, sqlite_vec.serialize_float32(vec)),
                    )
                conn.execute("UPDATE events SET status = 'processed' WHERE id = ?", (row["id"],))
            result["procesados"] += 1
            result["trozos"] += len(chunks)
        except Exception:
            log.exception("Fallo procesando %s", row["id"])
            _set_status(row["id"], "failed")
            result["fallidos"] += 1
    return result
