"""Lado del canal de la conversación con Isabelle (común a todos los canales).

- Solo atiende a los usuarios autorizados; a los demás los ignora en silencio.
- Cola con un candado por chat: si el jefe escribe varios mensajes seguidos mientras Isabelle
  prepara una respuesta, se responden todos juntos en una sola (POST /conversar con la lista).
- "escribiendo..." visible mientras piensa y una pausa humana según el largo de la respuesta.
- Sin comandos: todo texto va a /conversar; los archivos se suben y van a /conversar/archivo, donde
  Isabelle los mira o los escucha y responde; un álbum (varias fotos juntas) espera ALBUM_ESPERA s y
  se responde una sola vez. Los botones van a /conversar/boton y después se quitan del mensaje.
- Una respuesta con «seguir» (el aviso antes de buscar en internet) sale aparte y enseguida se
  pide y envía la continuación (POST /conversar/seguir).
- deliver_outbox() reparte lo que Isabelle quiere decir por su cuenta (avisos, resumen matutino).
Nunca registra el contenido de los mensajes.
"""

import asyncio
import logging
import time

from channels.base import Channel, Incoming, PermanentSendError
from channels.client import NexusClient, NexusError

log = logging.getLogger("nexus.channels")

TYPING_EVERY = 4  # Telegram borra "escribiendo..." a los 5 s
# Pausa humana: base + por carácter, con tope, descontando lo que ya tardó en pensar
PAUSA_BASE, PAUSA_POR_CARACTER, PAUSA_MAX = 0.6, 0.015, 3.5
FALLO = "⚠️ Algo falló de mi lado. Inténtelo de nuevo en un momento."
ALBUM_ESPERA = 1.5  # segundos para juntar las fotos de un álbum (llegan como mensajes separados)
RAFAGA_ESPERA = 1.2  # segundos que espera antes de responder: junta una ráfaga («oye» «una cosa» «¿…?»)


class Assistant:
    def __init__(self, channel: Channel, nexus: NexusClient, allowed_ids: set[str], pausa: bool = True):
        self.channel = channel
        self.nexus = nexus
        self.allowed = allowed_ids
        self.pausa = pausa
        self._locks: dict[str, asyncio.Lock] = {}
        self._pendientes: dict[str, list[str]] = {}
        self._albumes: dict[tuple[str, str], list[Incoming]] = {}
        self._tareas: set[asyncio.Task] = set()
        self.max_descarga: int | None = None  # bytes que el canal deja bajar (Telegram: 20 MB)
        self.rafaga_s = RAFAGA_ESPERA

    def authorized(self, msg: Incoming) -> bool:
        return msg.user_id in self.allowed

    # ---------- entrada ----------

    async def handle(self, msg: Incoming) -> list[str]:
        """Atiende un mensaje. Devuelve las respuestas enviadas ([] si se respondió junto con
        otro mensaje que ya estaba en curso, o si el usuario no está autorizado)."""
        if not self.authorized(msg):
            log.warning("Mensaje ignorado de un usuario no autorizado (id %s)", msg.user_id)
            return []
        chat = msg.chat_id
        lock = self._locks.setdefault(chat, asyncio.Lock())
        if msg.download is not None and msg.grupo:  # álbum: el primero espera a los demás
            clave = (chat, msg.grupo)
            primero = clave not in self._albumes
            self._albumes.setdefault(clave, []).append(msg)
            if not primero:
                return []
            await asyncio.sleep(ALBUM_ESPERA)
            grupo = self._albumes.pop(clave)
            async with lock:
                enviados = await self._atender(chat, self._archivos(grupo))
                return enviados + await self._vaciar(chat)
        if msg.button or msg.download is not None:
            async with lock:  # en orden con la conversación
                enviados = await self._atender(chat, self._boton(msg) if msg.button else self._archivos([msg]))
                return enviados + await self._vaciar(chat)
        if not msg.text.strip():
            return []
        self._pendientes.setdefault(chat, []).append(msg.text)
        if lock.locked():
            return []  # quien tiene el candado lo responde junto con lo suyo
        async with lock:
            if self.rafaga_s:  # los que lleguen en este rato van en la misma respuesta
                await asyncio.sleep(self.rafaga_s)
            return await self._vaciar(chat)

    async def _vaciar(self, chat: str) -> list[str]:
        """Responde los textos en cola (con el candado tomado), en tandas, hasta que no quede nada."""
        enviados: list[str] = []
        while self._pendientes.get(chat):
            textos = self._pendientes.pop(chat)
            if len(textos) > 1:
                log.info("Chat %s: %d mensajes seguidos -> una sola respuesta", chat, len(textos))
            enviados += await self._atender(chat, self.nexus.conversar(textos, self.channel.name, chat))
        return enviados

    async def _boton(self, msg: Incoming) -> dict:
        try:
            return await self.nexus.boton(msg.button, self.channel.name, msg.chat_id)
        finally:
            try:
                await self.channel.button_done(msg)
            except Exception as exc:
                log.debug("No se pudieron quitar los botones (%s)", type(exc).__name__)

    async def _archivos(self, msgs: list[Incoming]) -> dict:
        """Sube los archivos que llegaron juntos y pide una sola respuesta. Los que pasan del límite
        de descarga del canal no se bajan: Isabelle lo dice."""
        items, grande = [], False
        for msg in msgs:
            if self.max_descarga and (msg.size or 0) > self.max_descarga:
                grande = True
                continue
            data = await msg.download()
            name = msg.filename or ("nota_de_voz.ogg" if msg.is_voice else "archivo")
            r = await self.nexus.upload(data, name, msg.mime or "application/octet-stream", self.channel.name)
            items.append({"id": r["id"], "filename": name, "kind": r.get("kind") or "", "duplicate": bool(r.get("duplicate"))})
        leyenda = " ".join(m.text.strip() for m in msgs if m.text and m.text.strip())
        return await self.nexus.archivo(items, self.channel.name, msgs[0].chat_id, leyenda,
                                        any(m.is_voice for m in msgs), grande)

    async def _atender(self, chat: str, trabajo) -> list[str]:
        """Espera la respuesta con "escribiendo..." visible, hace la pausa humana y la envía."""
        inicio = time.monotonic()
        await self._typing(chat)
        latido = asyncio.create_task(self._keep_typing(chat))
        botones, seguir = None, False
        try:
            r = await trabajo
            texto, botones, seguir = r.get("texto") or "", r.get("botones"), bool(r.get("seguir"))
        except NexusError as exc:
            texto = f"⚠️ {exc}"
        except Exception:
            log.exception("Error atendiendo un mensaje")
            texto = FALLO
        try:
            if self.pausa:
                pausa = min(PAUSA_BASE + len(texto) * PAUSA_POR_CARACTER, PAUSA_MAX)
                restante = pausa - (time.monotonic() - inicio)
                if restante > 0:
                    await asyncio.sleep(restante)
        finally:
            latido.cancel()
        if not texto:
            return []
        try:
            await self.channel.send(chat, texto, botones)
        except Exception as exc:
            log.warning("No se pudo responder en %s (%s)", self.channel.name, type(exc).__name__)
            return []
        if seguir:  # un aviso («déjeme buscarlo…») va aparte y enseguida llega la continuación
            return [texto] + await self._atender(chat, self.nexus.seguir(self.channel.name, chat))
        return [texto]

    async def _typing(self, chat_id: str) -> None:
        try:
            await self.channel.typing(chat_id)
        except Exception as exc:  # nunca debe impedir responder
            log.debug("typing falló en %s (%s)", self.channel.name, type(exc).__name__)

    async def _keep_typing(self, chat_id: str) -> None:
        while True:
            await asyncio.sleep(TYPING_EVERY)
            await self._typing(chat_id)

    # ---------- salida (proactiva) ----------

    async def deliver_outbox(self) -> int:
        """Envía lo pendiente del buzón. Solo cuenta como entregado con el acuse de la plataforma
        (message_id); si falla (429, 400, chat bloqueado, sin red) se informa al núcleo, que lo
        reintenta con espera creciente (o la que pidió Telegram)."""
        sent = 0
        for m in await self.nexus.outbox(self.channel.name):
            targets = [m["chat_id"]] if m["chat_id"] else self.channel.owners()
            acuses = []
            try:
                for chat in targets:
                    acuses.append(await self.channel.send(chat, m["text"], m.get("botones")))
            except PermanentSendError as exc:  # p. ej. fuera de la ventana de WhatsApp: cada hora
                log.warning("Aviso %s no entregable por ahora: %s", m["id"], exc)
                await self.nexus.fallo(m["id"], f"permanente: {exc}"[:200], 3600)
                continue
            except Exception as exc:
                codigo = getattr(exc, "codigo", None) or type(exc).__name__
                log.warning("No se pudo entregar el aviso %s (%s); se reintenta", m["id"], codigo)
                await self.nexus.fallo(m["id"], str(codigo), getattr(exc, "retry_after", None))
                continue
            acuse = ",".join(str(a) for a in acuses if a)
            if not acuse:
                log.warning("Aviso %s sin acuse de la plataforma; se reintenta", m["id"])
                await self.nexus.fallo(m["id"], "sin acuse")
                continue
            await self.nexus.delivered(m["id"], acuse)
            if m.get("kind") == "reminder":
                log.info("Recordatorio %s entregado por %s", m.get("ref_id"), self.channel.name)
            sent += 1
        return sent

    async def run(self, outbox_every: float = 5) -> None:
        """Bucle principal: atiende mensajes (cada uno en su tarea, así los que llegan mientras
        Isabelle piensa entran a la cola) y reparte el buzón en paralelo."""

        async def outbox_loop():
            while True:
                try:
                    await self.deliver_outbox()
                except NexusError as exc:
                    log.warning("Buzón no disponible: %s", exc)
                except Exception:
                    log.exception("Error repartiendo el buzón")
                await asyncio.sleep(outbox_every)

        async def atender(msg: Incoming):
            try:
                await self.handle(msg)
            except Exception:
                log.exception("Error inesperado atendiendo un mensaje")

        task = asyncio.create_task(outbox_loop())
        try:
            async for msg in self.channel.receive():
                t = asyncio.create_task(atender(msg))
                self._tareas.add(t)
                t.add_done_callback(self._tareas.discard)
        finally:
            task.cancel()
