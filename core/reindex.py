"""/reindex: borra lo derivado (trozos, vectores, resúmenes) y lo regenera desde los originales.

Los originales en data/raw no se tocan. El worker vuelve a procesar y digerir los eventos.

Modo solo_vectores (Fase 4, cambio de modelo de embeddings): calcula los vectores de los trozos
que ya existen en la tabla nueva (db.VEC), sin volver a trocear, resumir ni analizar nada. Respaldo
verificado antes; avanza por lotes (GET /reindex muestra el progreso) y se puede retomar: solo hace
los trozos que aún no tienen vector. La tabla vieja (db.VEC_VIEJA) no se toca: borrar_vieja() la
quita solo cuando la nueva está completa.
"""

import asyncio
import logging

import sqlite_vec

from core import backup, clock, events, memory
from core.db import VEC, VEC_VIEJA, connect
from providers.embeddings import get_embedder
from providers.openrouter import ChatUnavailable

log = logging.getLogger("nexus.reindex")


def _reset(conn, event_id: str) -> int:
    n = memory.delete_chunks(conn, event_id)
    conn.execute("DELETE FROM digests WHERE event_id = ?", (event_id,))
    conn.execute(
        "UPDATE events SET status = 'pending', attempts = 0, error = NULL WHERE id = ?",
        (event_id,),
    )
    return n


async def reindex(event_id: str | None = None, everything: bool = False) -> dict:
    if not event_id and not everything:
        raise ValueError("Indica event_id o todos=true.")
    snapshot = None
    if everything:
        # Regenerar todo es grande: primero un respaldo verificado
        snapshot = (await backup.backup_now()).get("snapshot")
    with connect() as conn:
        if event_id:
            ids = [r["id"] for r in conn.execute("SELECT id FROM events WHERE id = ?", (event_id,))]
            if not ids:
                raise LookupError("No existe ese evento.")
        else:
            ids = [r["id"] for r in conn.execute("SELECT id FROM events WHERE status != 'pending'")]
    trozos = 0
    for eid in ids:
        with connect() as conn:  # una transacción por evento: no bloquea la base mucho rato
            trozos += _reset(conn, eid)
        events.publish("reindex", {"id": eid})
    log.info("Reindexación: %d eventos, %d trozos borrados", len(ids), trozos)
    return {"reencolados": len(ids), "trozos_borrados": trozos, "respaldo": snapshot}


LOTE = 64
progreso: dict = {"estado": "sin_iniciar", "hechos": 0, "total": 0, "inicio": None, "fin": None, "error": None}
_tarea: asyncio.Task | None = None


def estado_vectores() -> dict:
    with connect() as conn:
        trozos = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        con_vector = conn.execute(f"SELECT COUNT(*) FROM chunks c JOIN {VEC} v ON v.rowid = c.id").fetchone()[0]
        vieja = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (VEC_VIEJA,)).fetchone())
    return {"tabla": VEC, "trozos": trozos, "con_vector": con_vector, "completo": con_vector == trozos,
            "tabla_vieja": VEC_VIEJA if vieja else None, "reindexado": dict(progreso)}


def _sin_vector(limite: int) -> list:
    with connect() as conn:
        return conn.execute(f"SELECT c.id, c.content FROM chunks c LEFT JOIN {VEC} v ON v.rowid = c.id"
                            " WHERE v.rowid IS NULL ORDER BY c.id LIMIT ?", (limite,)).fetchall()


async def _vectores() -> None:
    embedder = get_embedder()
    try:
        while filas := _sin_vector(LOTE):
            vecs = await embedder.embed_documents([r["content"] for r in filas])
            with connect() as conn:
                for r, v in zip(filas, vecs):
                    conn.execute(f"INSERT OR REPLACE INTO {VEC} (rowid, embedding) VALUES (?, ?)",
                                 (r["id"], sqlite_vec.serialize_float32(v)))
            progreso["hechos"] += len(filas)
            events.publish("reindex", {"hechos": progreso["hechos"], "total": progreso["total"]})
        progreso["estado"] = "listo"
    except ChatUnavailable as exc:  # se retoma con otro POST: solo hace lo que falta
        progreso.update(estado="en_espera", error=str(exc))
    except Exception as exc:
        log.exception("Fallo calculando vectores")
        progreso.update(estado="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        progreso["fin"] = clock.now_utc().isoformat()
        log.info("Vectores: %d de %d (%s)", progreso["hechos"], progreso["total"], progreso["estado"])


async def solo_vectores() -> dict:
    """Arranca (o retoma) el cálculo de vectores de los trozos que no tienen en la tabla nueva."""
    global _tarea
    if _tarea and not _tarea.done():
        return estado_vectores()
    snapshot = (await backup.backup_now()).get("snapshot")
    falta = estado_vectores()
    progreso.update(estado="calculando", hechos=0, total=falta["trozos"] - falta["con_vector"],
                    inicio=clock.now_utc().isoformat(), fin=None, error=None, respaldo=snapshot)
    _tarea = asyncio.create_task(_vectores())
    return estado_vectores()


def borrar_vieja() -> dict:
    """Quita la tabla de vectores vieja, solo si la nueva tiene vector para todos los trozos."""
    e = estado_vectores()
    if not e["completo"]:
        raise ValueError(f"La tabla nueva no está completa ({e['con_vector']} de {e['trozos']}).")
    if e["tabla_vieja"]:
        with connect() as conn:
            conn.execute(f"DROP TABLE {VEC_VIEJA}")
        log.info("Tabla de vectores vieja %s borrada", VEC_VIEJA)
    return estado_vectores()
