import asyncio
import logging
import time

from config import settings
from core import events, medios, memory
from core.chunker import chunk_segments
from core.db import connect
from providers.embeddings import get_embedder
from providers.openrouter import ChatUnavailable
from senses import Unsupported

log = logging.getLogger("nexus.processor")

# Colas separadas: un audio largo no frena al texto. Cada cola tiene su candado para que el
# worker, /process y procesar_uno() (un archivo que llega por el chat) no procesen lo mismo a la vez.
QUEUES = {"texto": ("text", "document", "image"), "audio": ("audio", "video")}
_locks = {name: asyncio.Lock() for name in QUEUES}
# Eventos que se están procesando ahora (para la interfaz)
current: set[str] = set()

EMPTY_REASON = {
    "image": "No se vio nada en la imagen.",
    "video": "Video guardado sin analizar: los videos solo se guardan.",
    "audio": "No se reconoció voz clara en el audio.",
}
COLUMNS = "id, kind, raw_path, mime, status"


def _set_status(event_id: str, status: str, reason: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE events SET status = ?, error = ? WHERE id = ?", (status, reason, event_id)
        )
    if status != "processed":
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


def _vacio() -> dict:
    return {"procesados": 0, "sin_texto": 0, "no_soportados": 0, "a_reintentar": 0, "fallidos": 0,
            "trozos": 0, "en_espera": 0}


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
            f"SELECT {COLUMNS} FROM events"
            f" WHERE status = 'pending' AND kind IN ({','.join('?' * len(kinds))})"
            " ORDER BY created_at LIMIT ?",
            (*kinds, limit),
        ).fetchall()
    result = _vacio()
    for row in rows:
        try:
            await _procesar(dict(row), embedder, result)
        except ChatUnavailable:
            break  # OpenRouter no responde: lo demás espera a la próxima vuelta
    return result


async def procesar_uno(event_id: str, forzar: bool = False, rehacer: bool = False) -> dict | None:
    """Procesa ya este evento (un archivo que llegó por el chat, para comentarlo al llegar). Si ya
    estaba procesado no hace nada, salvo con forzar (el jefe pidió volver a mirarlo: nueva llamada
    a la IA) o rehacer (rearmar los trozos desde el análisis guardado, sin IA). Devuelve el
    evento con su estado final, o None si no existe. Lanza ChatUnavailable si OpenRouter no
    responde (el evento queda pendiente sin gastar un intento)."""
    with connect() as conn:
        row = conn.execute(f"SELECT {COLUMNS} FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None
    cola = next(n for n, kinds in QUEUES.items() if row["kind"] in kinds)
    async with _locks[cola]:
        with connect() as conn:
            row = conn.execute(f"SELECT {COLUMNS} FROM events WHERE id = ?", (event_id,)).fetchone()
        if row["status"] == "pending" or forzar or rehacer:
            await _procesar(dict(row), get_embedder(), _vacio(), forzar)
    with connect() as conn:
        return dict(conn.execute("SELECT id, kind, status, error FROM events WHERE id = ?", (event_id,)).fetchone())


async def _procesar(row: dict, embedder, result: dict, forzar: bool = False) -> None:
    events.publish("processing", {"id": row["id"]})
    current.add(row["id"])
    started = time.monotonic()
    try:
        try:
            segments = await medios.extraer(row, forzar)
        except Unsupported as e:
            _set_status(row["id"], "unsupported", f"{e}. El original está guardado; se puede reprocesar con /requeue.")
            result["no_soportados"] += 1
            return
        pieces = chunk_segments(segments)
        chunks = [text for text, _ in pieces]
        if not chunks:
            with connect() as conn:
                memory.delete_chunks(conn, row["id"])
            _set_status(row["id"], "empty", EMPTY_REASON.get(row["kind"], "No se encontró texto en el archivo."))
            result["sin_texto"] += 1
            return

        vectors = await embedder.embed_documents(chunks)
        with connect() as conn:
            # Restos de un intento anterior o de una reindexación: el resumen se rehace
            memory.delete_chunks(conn, row["id"])
            conn.execute("DELETE FROM digests WHERE event_id = ?", (row["id"],))
            for pos, ((content, meta), vec) in enumerate(zip(pieces, vectors)):
                memory.insert_chunk(conn, row["id"], pos, content, vec, meta)
            conn.execute("UPDATE events SET status = 'processed', error = NULL WHERE id = ?", (row["id"],))
        log.info("Procesado %s: %d trozos en %.1f s", row["id"], len(chunks), time.monotonic() - started)
        events.publish("processed", {"id": row["id"], "trozos": len(chunks)})
        result["procesados"] += 1
        result["trozos"] += len(chunks)
    except ChatUnavailable as exc:
        log.warning("Procesar %s queda en espera: OpenRouter no disponible (%s)", row["id"], exc.reason)
        result["en_espera"] += 1
        raise
    except Exception as exc:
        log.exception("Fallo procesando %s", row["id"])
        status = _register_failure(row["id"], f"{type(exc).__name__}: {exc}")
        events.publish("processing_error", {"id": row["id"], "estado": status, "error": str(exc)[:300]})
        result["fallidos" if status == "failed" else "a_reintentar"] += 1
    finally:
        current.discard(row["id"])
