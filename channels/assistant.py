"""Lógica de conversación común a todos los canales.

- Solo atiende a los usuarios autorizados; a los demás los ignora en silencio.
- "recuérdame ..." siempre se confirma con el usuario antes de guardar.
- /privado marca el siguiente envío (texto o archivo) como privado.
- deliver_outbox() reparte lo que NEXUS quiere decir (avisos, resumen matutino).
"""

import asyncio
import logging
from dataclasses import dataclass

from channels.base import Channel, Incoming, Intent, detect_intent
from channels.client import NexusClient, NexusError

log = logging.getLogger("nexus.channels")

MAX_SOURCES = 3


@dataclass
class ChatState:
    private_next: bool = False
    pending_reminder: dict | None = None


class Assistant:
    def __init__(self, channel: Channel, nexus: NexusClient, allowed_ids: set[str]):
        self.channel = channel
        self.nexus = nexus
        self.allowed = allowed_ids
        self.states: dict[str, ChatState] = {}
        self._profile: dict | None = None

    # ---------- entrada ----------

    def authorized(self, msg: Incoming) -> bool:
        return msg.user_id in self.allowed

    async def handle(self, msg: Incoming) -> list[str]:
        """Procesa un mensaje y devuelve las respuestas (ya enviadas por el canal)."""
        if not self.authorized(msg):
            log.warning("Mensaje ignorado de un usuario no autorizado (id %s)", msg.user_id)
            return []
        state = self.states.setdefault(msg.chat_id, ChatState())
        intent, arg = detect_intent(msg, awaiting_confirmation=state.pending_reminder is not None)
        if intent not in (Intent.YES, Intent.NO):
            state.pending_reminder = None  # cualquier otra cosa cancela la confirmación
        try:
            replies = await self._dispatch(intent, arg, msg, state)
        except NexusError as exc:
            replies = [f"⚠️ {exc}"]
        except Exception:
            log.exception("Error atendiendo un mensaje (%s)", intent.value)
            replies = ["⚠️ Algo falló de mi lado. Inténtalo de nuevo en un momento."]
        for text in replies:
            await self.channel.send(msg.chat_id, text)
        return replies

    async def _dispatch(self, intent: Intent, arg: str, msg: Incoming, state: ChatState) -> list[str]:
        if intent in (Intent.START, Intent.HELP):
            return [await self._help()]
        if intent == Intent.PRIVATE:
            if arg:
                await self.nexus.remember(arg, self.channel.name, private=True)
                return ["🔒 Guardado como privado. Nunca saldrá a la nube."]
            state.private_next = True
            return ["🔒 Lo siguiente que me envíes (texto con /recordar o archivo) será privado."]
        if intent == Intent.REMEMBER:
            if not arg:
                return ["Escribe lo que quieres que recuerde: /recordar Pedro paga los viernes."]
            private = self._take_private(state)
            await self.nexus.remember(arg, self.channel.name, private=private)
            return ["✅ Lo recordaré." + (" (privado)" if private else "")]
        if intent in (Intent.FILE, Intent.VOICE):
            return [await self._file(msg, state)]
        if intent == Intent.ASK:
            if not arg:
                return ["¿Qué quieres saber? Escríbeme la pregunta."]
            return [await self._ask(arg)]
        if intent == Intent.STATUS:
            return [await self._status()]
        if intent == Intent.TODAY:
            return [await self._today()]
        if intent == Intent.REMINDER:
            return [await self._reminder_parse(arg, state)]
        if intent == Intent.YES:
            return [await self._reminder_confirm(msg, state)]
        if intent == Intent.NO:
            state.pending_reminder = None
            return ["De acuerdo, no lo guardo."]
        if intent == Intent.REMINDERS:
            return [await self._reminders(msg)]
        if intent == Intent.DONE:
            return [await self._done(arg, msg)]
        return [await self._help()]

    def _take_private(self, state: ChatState) -> bool:
        private, state.private_next = state.private_next, False
        return private

    async def _profile_data(self) -> dict:
        if self._profile is None:
            self._profile = await self.nexus.profile()
        return self._profile

    async def _help(self) -> str:
        p = await self._profile_data()
        ejemplos = "\n".join(f"• {e}" for e in p.get("ejemplos", [])[:3])
        return (
            f"Hola, soy {p['asistente']['nombre']}. Aprendo de lo que me cuentas.\n\n"
            "• Escríbeme una pregunta y busco en mi memoria.\n"
            "• /recordar <texto> — guardo una nota.\n"
            "• Envíame archivos, fotos o notas de voz y los aprendo.\n"
            "• «recuérdame mañana a las 9 …» — te aviso (te pido confirmar).\n"
            "• /recordatorios — pendientes · /hecho [n] — marcar hecho.\n"
            "• /hoy — lo que aprendí hoy · /estado — cómo voy.\n"
            "• /privado — lo siguiente que envíes nunca sale a la nube."
            + (f"\n\nEjemplos:\n{ejemplos}" if ejemplos else "")
        )

    async def _file(self, msg: Incoming, state: ChatState) -> str:
        data = await msg.download()
        private = self._take_private(state)
        name = msg.filename or ("nota_de_voz.ogg" if msg.is_voice else "archivo")
        r = await self.nexus.upload(
            data, name, msg.mime or "application/octet-stream", self.channel.name, private
        )
        if r.get("duplicate"):
            return "Ya tenía ese archivo en mi memoria."
        what = "la nota de voz" if msg.is_voice else f"«{name}»"
        extra = " Lo transcribo en un momento." if r.get("kind") == "audio" else ""
        return f"📥 Recibí {what}.{extra}" + (" (privado)" if private else "")

    async def _ask(self, question: str) -> str:
        r = await self.nexus.ask(question)
        answer = r.get("answer", "")
        names = list(dict.fromkeys(s["filename"] for s in r.get("sources", []) if s.get("filename")))
        if names and "No tengo información" not in answer:
            answer += "\n\nFuentes: " + ", ".join(names[:MAX_SOURCES])
        return answer

    async def _status(self) -> str:
        g = await self.nexus.growth()
        a = g["aprendido"]
        return (
            f"🧠 {g['dias_de_vida']} días de vida · racha de {g['racha_dias']} días\n"
            f"• {a['elementos']} elementos, {a['recuerdos']} recuerdos, {a['conceptos']} conceptos\n"
            f"• Hoy: +{g['hoy']} · por procesar: {g['pendientes_de_digerir']}"
            + (f" · con error: {g['fallidos']}" if g.get("fallidos") else "")
        )

    async def _today(self) -> str:
        t = await self.nexus.today()
        lines = [f"📅 Hoy aprendí {t['total']} cosa(s)."]
        for e in t["elementos"][-10:]:
            # Lo privado no viaja por el canal: solo se menciona que existe
            detalle = "(privado)" if e["privado"] else (e["resumen"] or e["estado"])
            lines.append(f"• {e['hora']} {e['archivo']}: {detalle}")
        rem = t["recordatorios"]["hoy"]
        if rem:
            lines.append("\nRecordatorios de hoy:")
            lines += [f"• {r['cuando']} — {r['texto']} (/hecho {r['id']})" for r in rem]
        return "\n".join(lines)

    async def _reminder_parse(self, text: str, state: ChatState) -> str:
        r = await self.nexus.parse_reminder(text)
        state.pending_reminder = r
        nota = " (lo interpreté con el modelo; revísalo bien)" if r.get("metodo") == "llm" else ""
        return f"Entendí: {r['entendido']} — «{r['tarea']}»{nota}.\n¿Lo guardo? Responde sí o no."

    async def _reminder_confirm(self, msg: Incoming, state: ChatState) -> str:
        r, state.pending_reminder = state.pending_reminder, None
        if not r:
            return "No tengo nada pendiente de confirmar."
        saved = await self.nexus.create_reminder(r["tarea"], r["due_at"], self.channel.name, msg.chat_id)
        return f"⏰ Listo (n.º {saved['id']}): te aviso {r['entendido']}."

    async def _reminders(self, msg: Incoming) -> str:
        items = await self.nexus.reminders(msg.chat_id)
        if not items:
            return "No tienes recordatorios pendientes."
        return "Recordatorios pendientes:\n" + "\n".join(
            f"• {r['id']}) {r['due_local']} — {r['text']}" for r in items
        )

    async def _done(self, arg: str, msg: Incoming) -> str:
        rid = int(arg) if arg.strip().isdigit() else None
        r = await self.nexus.done(rid, msg.chat_id)
        return f"✅ Hecho: {r['text']}."

    # ---------- salida (proactiva) ----------

    async def deliver_outbox(self) -> int:
        """Envía lo pendiente del buzón; solo confirma lo que se entregó."""
        sent = 0
        for m in await self.nexus.outbox(self.channel.name):
            targets = [m["chat_id"]] if m["chat_id"] else self.channel.owners()
            try:
                for chat in targets:
                    await self.channel.send(chat, m["text"])
            except Exception as exc:
                log.warning("No se pudo entregar el aviso %s (%s); se reintenta", m["id"], type(exc).__name__)
                continue
            await self.nexus.delivered(m["id"])
            sent += 1
        return sent

    async def run(self, outbox_every: float = 15) -> None:
        """Bucle principal: atiende mensajes y reparte el buzón en paralelo."""

        async def outbox_loop():
            while True:
                try:
                    await self.deliver_outbox()
                except NexusError as exc:
                    log.warning("Buzón no disponible: %s", exc)
                except Exception:
                    log.exception("Error repartiendo el buzón")
                await asyncio.sleep(outbox_every)

        task = asyncio.create_task(outbox_loop())
        try:
            async for msg in self.channel.receive():
                await self.handle(msg)
        finally:
            task.cancel()
