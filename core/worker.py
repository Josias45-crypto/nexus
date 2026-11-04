import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import settings
from core import backup, charla, conversa, digest, iniciativa, processor, reminders

log = logging.getLogger("nexus.worker")

state: dict = {
    "activo": False,
    "ultima_revision": None,
    "ultimo_resultado": None,
    "ultimo_audio": None,
    "ultimo_aviso": None,
    "ultima_digestion": None,
    "ultimo_respaldo": None,
    "ultima_memoria": None,
}
MEMORIA_CADA = 60  # segundos entre revisiones de la memoria del jefe


async def _process_step() -> dict:
    result = await processor.process_pending(limit=5, queue="texto")
    state["ultima_revision"] = datetime.now(timezone.utc).isoformat()
    return result


async def _audio_step() -> dict:
    # De uno en uno: un audio largo se transcribe por tramos (core/medios.py)
    return await processor.process_pending(limit=1, queue="audio")


async def _digest_step() -> dict:
    return await digest.digest_pending(limit=5)


async def _scheduler_step() -> dict:
    result = await asyncio.to_thread(reminders.due_step)
    try:  # saludos de mañana y noche (reemplazan al resumen matutino de core/briefing.py)
        result.update(await iniciativa.due_step())
    except Exception:
        log.exception("Error en los saludos de Isabelle")
    return result


async def _memoria_step() -> dict:
    return await charla.revisar(conversa.ocupado)


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
        tasks.append(
            asyncio.create_task(
                _loop("programador", _scheduler_step, "ultimo_aviso", settings.SCHEDULER_INTERVAL)
            )
        )
        tasks.append(asyncio.create_task(_loop("memoria", _memoria_step, "ultima_memoria", MEMORIA_CADA)))
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
