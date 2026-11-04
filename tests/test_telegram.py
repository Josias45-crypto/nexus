"""Canal de Telegram con la red simulada (httpx.MockTransport): no llama a Telegram real ni al núcleo.

Se ejecuta dentro de la imagen (tests/e2e.py lo hace así):
    docker run --rm -v "$PWD/tests:/app/tests:ro" isabelle-core python -m unittest tests.test_telegram
"""

import asyncio
import json
import logging
import unittest

import httpx

from channels.client import NexusClient
from channels.telegram import TelegramAssistant, TelegramChannel

TOKEN = "123456:SECRETO-DE-PRUEBA"
OWNER = 1001
BOTONES = [[{"texto": "Cambiar", "dato": "rec:cambiar:5"}, {"texto": "Cancelar", "dato": "rec:cancelar:5"}]]


def _msg(uid: int, **extra) -> dict:
    return {"message_id": 7, "from": {"id": uid}, "chat": {"id": uid}, **extra}


class FakeTelegram:
    def __init__(self, updates: list[dict]):
        self.updates = updates
        self.sent: list[tuple[str, str]] = []
        self.markups: list[dict | None] = []
        self.actions: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith(f"/file/bot{TOKEN}/"):
            self.downloads += 1
            return httpx.Response(200, content=b"contenido del archivo")
        method = path.rsplit("/", 1)[-1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": ups})
        if method == "sendMessage":
            self.sent.append((str(body["chat_id"]), body["text"]))
            self.markups.append(body.get("reply_markup"))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "sendChatAction":
            self.actions.append((str(body["chat_id"]), body["action"]))
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f.ogg"}})
        if method in ("answerCallbackQuery", "editMessageReplyMarkup"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})


class FakeNexus:
    """Núcleo simulado: registra lo que le pide el canal. `espera` retrasa /conversar."""

    def __init__(self, espera: float = 0):
        self.turnos: list[list[str]] = []
        self.botones: list[str] = []
        self.uploads: list[dict] = []
        self.archivos: list[dict] = []
        self.espera = espera

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        body = json.loads(request.content) if request.headers.get("content-type") == "application/json" else {}
        if p == "/conversar":
            self.turnos.append(body["mensajes"])
            await asyncio.sleep(self.espera)
            botones = BOTONES if "recuérdame" in body["mensajes"][0] else None
            return httpx.Response(200, json={"texto": "Los martes, jefe.", "botones": botones})
        if p == "/conversar/boton":
            self.botones.append(body["dato"])
            return httpx.Response(200, json={"texto": "Cancelado, jefe: llamar a Piter.", "botones": None})
        if p == "/inbox/file":
            self.uploads.append(dict(request.url.params))
            return httpx.Response(200, json={"id": "x", "kind": "audio", "duplicate": False})
        if p == "/conversar/archivo":
            self.archivos.append(body)
            if body.get("grande") and not body["archivos"]:
                return httpx.Response(200, json={"texto": "Ese archivo pesa más de 20 MB, jefe.", "botones": None})
            return httpx.Response(200, json={"texto": f"Vi {len(body['archivos'])} archivo(s), jefe.", "botones": None})
        if p == "/outbox":
            return httpx.Response(200, json=[{"id": 1, "chat_id": None, "text": "⏰ Recordatorio: algo"}])
        if p == "/outbox/1/delivered":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "no"})


class TelegramTest(unittest.TestCase):
    def make(self, updates, espera: float = 0):
        self.tg = FakeTelegram(updates)
        self.nx = FakeNexus(espera)
        self.channel = TelegramChannel(TOKEN, [str(OWNER)], transport=httpx.MockTransport(self.tg))
        nexus = NexusClient("http://nexus", transport=httpx.MockTransport(self.nx))
        self.bot = TelegramAssistant(self.channel, nexus, {str(OWNER)}, pausa=False)

    def _run(self, n: int, logs: list[str]):
        async def go():
            gen = self.channel.receive()
            for _ in range(n):
                await self.bot.handle(await gen.__anext__())
            await self.bot.deliver_outbox()

        class Capture(logging.Handler):
            def emit(self, record):
                logs.append(self.format(record))

        h = Capture()
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            asyncio.run(go())
        finally:
            logging.getLogger().removeHandler(h)

    def test_conversation(self):
        self.make([
            {"update_id": 1, "message": _msg(OWNER, text="¿Cuándo entrega el proveedor?")},
            {"update_id": 2, "message": _msg(6666, text="hola")},
            {"update_id": 3, "message": _msg(OWNER, voice={"file_id": "v1", "file_size": 900})},
            {"update_id": 4, "message": _msg(6666, voice={"file_id": "v2", "file_size": 900})},
            {"update_id": 5, "message": _msg(OWNER, document={"file_id": "d", "file_size": 30_000_000})},
        ])
        logs: list[str] = []
        self._run(5, logs)
        to_owner = [t for c, t in self.tg.sent if c == str(OWNER)]
        self.assertEqual(self.nx.turnos, [["¿Cuándo entrega el proveedor?"]], "sin comandos: todo va a /conversar")
        self.assertIn("Los martes, jefe.", to_owner)
        self.assertFalse(any(c == "6666" for c, _ in self.tg.sent), "respondió a un extraño")
        self.assertEqual(self.tg.downloads, 1, "solo descarga archivos del dueño")
        voz, grande = self.nx.archivos
        self.assertTrue(voz["voz"] and len(voz["archivos"]) == 1, "la nota de voz va a Isabelle")
        self.assertTrue(grande["grande"] and grande["archivos"] == [], "el de más de 20 MB no se baja")
        self.assertTrue(any("20 MB" in t for t in to_owner))
        self.assertTrue(any("Recordatorio" in t for t in to_owner), "no repartió el buzón")
        self.assertFalse(any(TOKEN in line for line in logs), "el token apareció en el registro")
        self.assertFalse(any("Cuándo entrega" in line for line in logs), "el contenido apareció en el registro")
        self.assertIn((str(OWNER), "typing"), self.tg.actions, "no mostró 'escribiendo...'")

    def test_botones(self):
        self.make([
            {"update_id": 1, "message": _msg(OWNER, text="recuérdame hoy a las 5 pm llamar a Piter")},
            {"update_id": 2, "callback_query": {"id": "cb1", "from": {"id": OWNER}, "data": "rec:cancelar:5",
                                                "message": _msg(OWNER, message_id=9)}},
            {"update_id": 3, "callback_query": {"id": "cb2", "from": {"id": 6666}, "data": "rec:cancelar:5",
                                                "message": _msg(6666, message_id=9)}},
        ])
        self._run(3, [])
        markup = self.tg.markups[0]
        self.assertEqual([b["text"] for b in markup["inline_keyboard"][0]], ["Cambiar", "Cancelar"])
        self.assertEqual(markup["inline_keyboard"][0][1]["callback_data"], "rec:cancelar:5")
        self.assertEqual(self.nx.botones, ["rec:cancelar:5"], "el extraño no puede pulsar")
        self.assertIn("answerCallbackQuery", self.tg.calls)
        self.assertIn("editMessageReplyMarkup", self.tg.calls, "no quitó los botones")
        self.assertIn("Cancelado, jefe: llamar a Piter.", [t for _, t in self.tg.sent])

    def test_album_una_sola_respuesta(self):
        """Las fotos de un álbum llegan como mensajes separados con el mismo media_group_id."""
        self.make([])
        foto = lambda n: self.channel.to_incoming(_msg(OWNER, message_id=n, media_group_id="alb1",  # noqa: E731
                                                       photo=[{"file_id": f"f{n}", "file_size": 900}],
                                                       **({"caption": "las del local"} if n == 1 else {})))

        async def go():
            return await asyncio.gather(*(self.bot.handle(foto(n)) for n in (1, 2, 3)))

        respuestas = asyncio.run(go())
        self.assertEqual(len(self.nx.archivos), 1, "una sola llamada para el álbum")
        self.assertEqual(len(self.nx.archivos[0]["archivos"]), 3)
        self.assertEqual(self.nx.archivos[0]["leyenda"], "las del local")
        self.assertEqual(sum(len(r) for r in respuestas), 1, "una sola respuesta")

    def test_cola_junta_mensajes_seguidos(self):
        self.make([], espera=0.3)
        ch = self.channel

        async def go():
            m = lambda t: ch.to_incoming(_msg(OWNER, text=t))  # noqa: E731
            primero = asyncio.create_task(self.bot.handle(m("hola")))
            await asyncio.sleep(0.05)  # Isabelle ya está pensando el primero
            r2 = await asyncio.gather(self.bot.handle(m("te cuento algo")), self.bot.handle(m("¿me ayudas?")))
            return await primero, r2

        primero, (r2, r3) = asyncio.run(go())
        self.assertEqual(self.nx.turnos, [["hola"], ["te cuento algo", "¿me ayudas?"]])
        self.assertEqual((r2, r3), ([], []), "los seguidos se responden juntos, no por separado")
        self.assertEqual(len(primero), 2, "una respuesta por tanda")


if __name__ == "__main__":
    unittest.main()
