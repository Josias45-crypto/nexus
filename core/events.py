"""Bus de eventos en memoria para la vista en vivo (/brain/stream).

Solo viajan ids y metadatos, nunca contenido. Si nadie escucha, los eventos se descartan.
publish() se puede llamar desde el event loop o desde hilos (endpoints síncronos, to_thread).
"""

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger("nexus.events")

QUEUE_SIZE = 200  # por suscriptor; si un cliente se atrasa, se pierden sus eventos más nuevos

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _dispatch(msg: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def publish(tipo: str, datos: dict) -> None:
    if not _subscribers or _loop is None:
        return
    msg = {"tipo": tipo, "ts": datetime.now(timezone.utc).isoformat(), **datos}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is _loop:
            _dispatch(msg)
        else:
            _loop.call_soon_threadsafe(_dispatch, msg)
    except RuntimeError:  # loop cerrado (apagado)
        log.debug("Evento %s descartado: loop cerrado", tipo)


def subscribe() -> asyncio.Queue:
    global _loop
    _loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)
