import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import settings
from core import backup, digest, processor

log = logging.getLogger("nexus.worker")

state: dict = {
    "activo": False,
    "ultima_revision": None,
    "ultimo_resultado": None,
    "ultimo_audio": None,
    "ultima_digestion": None,
    "ultimo_respaldo": None,
}


async def _process_step() -> dict:
    result = await processor.process_pending(limit=5, queue="texto")
    state["ultima_revision"] = datetime.now(timezone.utc).isoformat()
    return result


async def _audio_step() -> dict:
    # De uno en uno: Whisper transcribe un audio a la vez
    return await processor.process_pending(limit=1, queue="audio")


async def _digest_step() -> dict:
    return await digest.digest_pending(limit=5)


async def _backup_step() -> dict:
    if not backup.is_due():
        return {}
    return await backup.backup_now()


async def _loop(label: str, step, result_key: str, interval: int) -> None:
    log.info("%s iniciado (revisa cada %s s)", label, interval)
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
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app):
    tasks: list[asyncio.Task] = []
    if settings.WORKER_ENABLED:
        state["activo"] = True
        every = settings.WORKER_INTERVAL
        tasks.append(asyncio.create_task(_loop("procesador", _process_step, "ultimo_resultado", every)))
        tasks.append(asyncio.create_task(_loop("oído", _audio_step, "ultimo_audio", every)))
        if settings.DIGEST_ENABLED:
            tasks.append(asyncio.create_task(_loop("digestor", _digest_step, "ultima_digestion", every)))
        if settings.BACKUP_HOURS > 0:
            tasks.append(asyncio.create_task(_loop("respaldo", _backup_step, "ultimo_respaldo", 300)))
    yield
    log.info("Apagando: se detiene el worker (lo que quedó a medias vuelve a la cola)")
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
