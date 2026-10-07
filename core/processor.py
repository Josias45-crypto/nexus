import asyncio
import logging
from pathlib import Path

import sqlite_vec

from config import settings
from core import events
from core.chunker import chunk_text
from core.db import connect
from providers.embeddings import get_embedder

log = logging.getLogger("nexus.processor")

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}

# Colas separadas: un audio largo no frena al texto. Cada cola tiene su candado para que
# el worker y una llamada manual a /process no procesen lo mismo a la vez.
QUEUES = {"texto": ("text", "document"), "audio": ("audio",)}
_locks = {name: asyncio.Lock() for name in QUEUES}


def extract_text(path: Path, mime: str) -> str | None:
    ext = path.suffix.lower()
    if mime.startswith("audio/") or ext in AUDIO_EXT:
        from core.transcriber import transcribe

        return transcribe(str(path))
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # Muchos PDF vienen cifrados solo con contraseña de propietario (vacía al abrir)
            try:
                opened = reader.decrypt("")
            except Exception:
                opened = 0
            if not opened:
                raise ValueError("PDF protegido con contraseña")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if ext in TEXT_EXT or mime.startswith("text/"):
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def _set_status(event_id: str, status: str, reason: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE events SET status = ?, error = ? WHERE id = ?", (status, reason, event_id)
        )


def _register_failure(event_id: str, error: str) -> str:
    """Suma un intento. Vuelve a 'pending' hasta agotar MAX_ATTEMPTS."""
    with connect() as conn:
        attempts = (
            conn.execute("SELECT attempts FROM events WHERE id = ?", (event_id,)).fetchone()[
                "attempts"
            ]
            + 1
        )
        status = "failed" if attempts >= settings.MAX_ATTEMPTS else "pending"
        conn.execute(
            "UPDATE events SET attempts = ?, status = ?, error = ? WHERE id = ?",
            (attempts, status, error[:500], event_id),
        )
    return status


async def process_pending(limit: int = 10, queue: str | None = None) -> dict:
    """Procesa una cola ("texto" o "audio") o, sin cola, ambas una tras otra."""
    total: dict = {}
    for name in [queue] if queue else list(QUEUES):
        async with _locks[name]:
            for key, value in (await _process_pending(limit, QUEUES[name])).items():
                total[key] = total.get(key, 0) + value
    return total


async def _process_pending(limit: int, kinds: tuple[str, ...]) -> dict:
    embedder = get_embedder()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, raw_path, mime FROM events"
            f" WHERE status = 'pending' AND kind IN ({','.join('?' * len(kinds))})"
            " ORDER BY created_at LIMIT ?",
            (*kinds, limit),
        ).fetchall()

    result = {
        "procesados": 0,
        "sin_texto": 0,
        "no_soportados": 0,
        "a_reintentar": 0,
        "fallidos": 0,
        "trozos": 0,
    }
    for row in rows:
        events.publish("processing", {"id": row["id"]})
        try:
            text = await asyncio.to_thread(
                extract_text, Path(row["raw_path"]), row["mime"] or ""
            )
            if text is None:
                ext = Path(row["raw_path"]).suffix.lower() or "sin extensión"
                _set_status(
                    row["id"],
                    "unsupported",
                    f"Formato no soportado todavía ({ext}, {row['mime'] or 'tipo desconocido'})."
                    " El original está guardado; se puede reprocesar con /requeue.",
                )
                result["no_soportados"] += 1
                continue
            chunks = chunk_text(text)
            if not chunks:
                _set_status(row["id"], "empty", "No se encontró texto en el archivo.")
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
                conn.execute(
                    "UPDATE events SET status = 'processed', error = NULL WHERE id = ?",
                    (row["id"],),
                )
            events.publish("processed", {"id": row["id"], "trozos": len(chunks)})
            result["procesados"] += 1
            result["trozos"] += len(chunks)
        except Exception as exc:
            log.exception("Fallo procesando %s", row["id"])
            status = _register_failure(row["id"], f"{type(exc).__name__}: {exc}")
            events.publish("processing_error", {"id": row["id"], "estado": status})
            if status == "failed":
                result["fallidos"] += 1
            else:
                result["a_reintentar"] += 1
    return result
