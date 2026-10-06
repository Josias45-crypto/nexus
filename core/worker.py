import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import settings
from core import digest, processor

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexus.worker")

state: dict = {
    "activo": False,
    "ultima_revision": None,
    "ultimo_resultado": None,
    "ultima_digestion": None,
}


async def _process_step() -> dict:
    result = await processor.process_pending(limit=5)
    state["ultima_revision"] = datetime.now(timezone.utc).isoformat()
    return result


async def _digest_step() -> dict:
    return await digest.digest_pending(limit=5)


async def _loop(label: str, step, result_key: str) -> None:
    log.info("%s iniciado (cada %s s)", label, settings.WORKER_INTERVAL)
    while True:
        try:
            result = await step()
            if any(result.values()):
                state[result_key] = result
                log.info("%s: %s", label, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error en %s", label)
        await asyncio.sleep(settings.WORKER_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    tasks: list[asyncio.Task] = []
    if settings.WORKER_ENABLED:
        state["activo"] = True
        tasks.append(asyncio.create_task(_loop("procesador", _process_step, "ultimo_resultado")))
        if settings.DIGEST_ENABLED:
            tasks.append(asyncio.create_task(_loop("digestor", _digest_step, "ultima_digestion")))
    yield
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
