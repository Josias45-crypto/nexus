"""/reindex: borra lo derivado (trozos, vectores, resúmenes) y lo regenera desde los originales.

Los originales en data/raw no se tocan. El worker vuelve a procesar y digerir los eventos.
"""

import logging

from core import backup, events, memory
from core.db import connect

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
