import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import settings
from core import processor

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexus.worker")

state: dict = {"activo": False, "ultima_revision": None, "ultimo_resultado": None}


async def run_forever() -> None:
    state["activo"] = True
    log.info("Worker iniciado (cada %s s)", settings.WORKER_INTERVAL)
    while True:
        try:
            result = await processor.process_pending(limit=5)
            state["ultima_revision"] = datetime.now(timezone.utc).isoformat()
            if any(result.values()):
                state["ultimo_resultado"] = result
                log.info("Worker: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error en el worker")
        await asyncio.sleep(settings.WORKER_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run_forever()) if settings.WORKER_ENABLED else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
