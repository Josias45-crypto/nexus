import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from config import settings
from core import backup, charla, clock, conversa, digest, events, iniciativa, outbox, processor, reminders

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
    "latido_programador": None,  # marca de vida del ciclo de recordatorios (UTC)
    "vigilante": None,
}
MEMORIA_CADA = 60  # segundos entre revisiones de la memoria del jefe
VIGILANTE_CADA = 30
_latido = 0.0  # time.monotonic() de la última vuelta del programador de recordatorios
_tareas: dict[str, asyncio.Task] = {}


async def _process_step() -> dict:
    result = await processor.process_pending(limit=5, queue="texto")
    state["ultima_revision"] = datetime.now(timezone.utc).isoformat()
    return result


async def _audio_step() -> dict:
    # De uno en uno: un audio largo se transcribe por tramos (core/medios.py)
    return await processor.process_pending(limit=1, queue="audio")


async def _digest_step() -> dict:
    return await digest.digest_pending(limit=5)


async def _recordatorios_loop() -> None:
    """Recordatorios en su propio ciclo, sin IA y sin esperar a nadie: despierta justo a la hora del
    próximo aviso (como mucho cada SCHEDULER_INTERVAL) y deja su marca de vida en cada vuelta.
    Antes compartía ciclo con los saludos, que esperan a la IA: un saludo lento (o OpenRouter caído
    con sus reintentos) retrasaba los avisos varios minutos."""
    global _latido
    log.info("Programador de recordatorios iniciado (revisa como mucho cada %s s)", settings.SCHEDULER_INTERVAL)
    while True:
        try:
            result = await asyncio.to_thread(reminders.due_step)
            if result.get("avisos"):
                state["ultimo_aviso"] = result
                log.info("programador: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error en el programador de recordatorios")
        _latido = time.monotonic()
        state["latido_programador"] = datetime.now(timezone.utc).isoformat()
        espera = settings.SCHEDULER_INTERVAL
        with contextlib.suppress(Exception):
            if prox := await asyncio.to_thread(reminders.proximo):
                espera = min(espera, max(1.0, (prox - clock.now_utc()).total_seconds() + 0.5))
        await asyncio.sleep(espera)


async def _saludos_step() -> dict:
    # Saludos de mañana y noche: pueden tardar (IA), por eso van aparte de los recordatorios
    return await iniciativa.due_step()


def _arrancar_programador() -> None:
    _tareas["programador"] = asyncio.create_task(_recordatorios_loop())


def salud_programador() -> dict:
    """Para /health: edad de la marca de vida y recordatorios vencidos sin salir al buzón."""
    edad = round(time.monotonic() - _latido, 1) if _latido else None
    return {"latido_hace_s": edad, "vivo": edad is not None and edad <= _limite_latido(),
            "recordatorios_atrasados": len(reminders.atrasados()), **outbox.estado()}


def _limite_latido() -> float:
    return max(90.0, settings.SCHEDULER_INTERVAL * 3)


async def vigilar_una_vez() -> dict:
    """Una vuelta del vigilante: si la tarea del programador murió o dejó de latir, la vuelve a
    arrancar; si hay un recordatorio vencido hace más de 2 min sin salir al buzón, lo reenvía ya
    («(retrasado)»), lo registra y avisa al jefe."""
    hecho: dict = {}
    t = _tareas.get("programador")
    muerto = t is None or t.done()
    if muerto or (_latido and time.monotonic() - _latido > _limite_latido()):
        log.error("Vigilante: el programador de recordatorios %s; lo reinicio",
                  "murió" if muerto else f"no late hace {time.monotonic() - _latido:.0f} s")
        if t and not t.done():
            t.cancel()
        _arrancar_programador()
        hecho["reinicio"] = True
    if ids := await asyncio.to_thread(reminders.atrasados):
        log.error("Vigilante: %d recordatorio(s) vencido(s) hace más de 2 min sin enviar: %s; los reenvío ya", len(ids), ids)
        r = await asyncio.to_thread(reminders.due_step)
        events.publish("alerta", {"tipo": "recordatorios_atrasados", "ids": ids})
        await asyncio.to_thread(_alertar, f"⚠ Jefe, se me atrasaron {len(ids)} aviso(s); ya se los mandé con «(retrasado)».")
        hecho.update(reenviados=r.get("avisos", 0), ids=ids)
    if hecho:
        state["vigilante"] = {"cuando": datetime.now(timezone.utc).isoformat(), **hecho}
    return hecho


async def _vigilante_loop() -> None:
    while True:
        await asyncio.sleep(VIGILANTE_CADA)
        try:
            await vigilar_una_vez()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error en el vigilante")


def _alertar(texto: str) -> None:
    from core.db import connect

    with connect() as conn:
        outbox.put(conn, settings.DEFAULT_CHANNEL, None, "alerta", None, texto)


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
        _arrancar_programador()
        tasks.append(asyncio.create_task(_vigilante_loop()))
        tasks.append(asyncio.create_task(_loop("saludos", _saludos_step, "ultimo_saludo", settings.SCHEDULER_INTERVAL)))
        tasks.append(asyncio.create_task(_loop("memoria", _memoria_step, "ultima_memoria", MEMORIA_CADA)))
        if settings.DIGEST_ENABLED:
            tasks.append(asyncio.create_task(_loop("digestor", _digest_step, "ultima_digestion", every)))
        if settings.BACKUP_HOURS > 0:
            tasks.append(asyncio.create_task(_loop("respaldo", _backup_step, "ultimo_respaldo", 300)))
    yield
    log.info("Apagando: se detiene el worker (lo que quedó a medias vuelve a la cola)")
    tasks += [t for t in _tareas.values() if t]
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
