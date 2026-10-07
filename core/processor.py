import asyncio
import logging
import time
from pathlib import Path

from config import settings
from core import events, memory
from core.chunker import chunk_segments
from core.db import connect
from providers.embeddings import get_embedder
from senses import Unsupported, extract

log = logging.getLogger("nexus.processor")

# Colas separadas: un audio o video largo no frena al texto. Cada cola tiene su candado para
# que el worker y una llamada manual a /process no procesen lo mismo a la vez.
QUEUES = {"texto": ("text", "document", "image"), "audio": ("audio", "video")}
_locks = {name: asyncio.Lock() for name in QUEUES}
# Eventos que se están procesando ahora (para la interfaz)
current: set[str] = set()

EMPTY_REASON = {
    "image": "No se encontró texto en la imagen. Para describir fotos sin texto, activa"
    " NEXUS_VISION_MODEL (docs/FORMATOS.md).",
    "video": "El video no tiene audio con voz.",
    "audio": "No se reconoció voz en el audio.",
}


def _set_status(event_id: str, status: str, reason: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE events SET status = ?, error = ? WHERE id = ?", (status, reason, event_id)
        )
    events.publish("processing_error", {"id": event_id, "estado": status, "error": reason})


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
            "SELECT id, kind, raw_path, mime FROM events"
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
        current.add(row["id"])
        started = time.monotonic()
        try:
            try:
                segments = await asyncio.to_thread(
                    extract, Path(row["raw_path"]), row["mime"] or ""
                )
            except Unsupported as e:
                _set_status(
                    row["id"],
                    "unsupported",
                    f"{e}. El original está guardado; se puede reprocesar con /requeue.",
                )
                result["no_soportados"] += 1
                continue
            pieces = chunk_segments(segments)
            chunks = [text for text, _ in pieces]
            if not chunks:
                _set_status(
                    row["id"], "empty", EMPTY_REASON.get(row["kind"], "No se encontró texto en el archivo.")
                )
                result["sin_texto"] += 1
                continue

            vectors = await embedder.embed_documents(chunks)
            with connect() as conn:
                # Restos de un intento anterior o de una reindexación: el resumen se rehace
                memory.delete_chunks(conn, row["id"])
                conn.execute("DELETE FROM digests WHERE event_id = ?", (row["id"],))
                for pos, ((content, meta), vec) in enumerate(zip(pieces, vectors)):
                    memory.insert_chunk(conn, row["id"], pos, content, vec, meta)
                conn.execute(
                    "UPDATE events SET status = 'processed', error = NULL WHERE id = ?",
                    (row["id"],),
                )
            log.info(
                "Procesado %s: %d trozos en %.1f s",
                row["id"], len(chunks), time.monotonic() - started,
            )
            events.publish("processed", {"id": row["id"], "trozos": len(chunks)})
            result["procesados"] += 1
            result["trozos"] += len(chunks)
        except Exception as exc:
            log.exception("Fallo procesando %s", row["id"])
            status = _register_failure(row["id"], f"{type(exc).__name__}: {exc}")
            events.publish("processing_error", {"id": row["id"], "estado": status, "error": str(exc)[:300]})
            if status == "failed":
                result["fallidos"] += 1
            else:
                result["a_reintentar"] += 1
        finally:
            current.discard(row["id"])
    return result
