"""Canal de Telegram con la red simulada (httpx.MockTransport): no llama a Telegram real.

Requiere httpx: se ejecuta dentro de la imagen (tests/e2e.py lo hace así):
    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_telegram
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


def _msg(uid: int, **extra) -> dict:
    return {"message_id": 7, "from": {"id": uid}, "chat": {"id": uid}, **extra}


class FakeTelegram:
    def __init__(self, updates: list[dict]):
        self.updates = updates
        self.sent: list[tuple[str, str]] = []
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith(f"/file/bot{TOKEN}/"):
            self.downloads += 1
            return httpx.Response(200, content=b"contenido del archivo")
        method = path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": ups})
        if method == "sendMessage":
            self.sent.append((str(body["chat_id"]), body["text"]))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f.ogg"}})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})


class FakeNexus:
    def __init__(self):
        self.uploads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/profile":
            return httpx.Response(200, json={"asistente": {"nombre": "NEXUS"}, "ejemplos": []})
        if p == "/ask":
            return httpx.Response(200, json={"answer": "Los martes.", "sources": [{"filename": "nota.txt"}]})
        if p == "/inbox/file":
            self.uploads.append(dict(request.url.params))
            return httpx.Response(200, json={"id": "x", "kind": "audio", "duplicate": False})
        if p == "/outbox":
            return httpx.Response(200, json=[{"id": 1, "chat_id": None, "text": "⏰ Recordatorio: algo"}])
        if p == "/outbox/1/delivered":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "no"})


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram(
            [
                {"update_id": 1, "message": _msg(OWNER, text="¿Cuándo entrega el proveedor?")},
                {"update_id": 2, "message": _msg(6666, text="hola")},
                {"update_id": 3, "message": _msg(OWNER, text="/privado")},
                {"update_id": 4, "message": _msg(OWNER, voice={"file_id": "v1", "file_size": 900})},
                {"update_id": 5, "message": _msg(6666, voice={"file_id": "v2", "file_size": 900})},
                {"update_id": 6, "message": _msg(OWNER, document={"file_id": "d", "file_size": 30_000_000})},
            ]
        )
        self.nx = FakeNexus()
        self.channel = TelegramChannel(TOKEN, [str(OWNER)], transport=httpx.MockTransport(self.tg))
        self.nexus = NexusClient("http://nexus", transport=httpx.MockTransport(self.nx))
        self.bot = TelegramAssistant(self.channel, self.nexus, {str(OWNER)})

    def _run(self, logs: list[str]):
        async def go():
            gen = self.channel.receive()
            for _ in range(6):
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
        logs: list[str] = []
        self._run(logs)
        to_owner = [t for c, t in self.tg.sent if c == str(OWNER)]
        self.assertTrue(any("Los martes" in t and "nota.txt" in t for t in to_owner))
        self.assertFalse(any(c == "6666" for c, _ in self.tg.sent), "respondió a un extraño")
        self.assertEqual(self.tg.downloads, 1, "solo descarga archivos del dueño")
        self.assertEqual(self.nx.uploads[0].get("private"), "true", "/privado no se aplicó a la voz")
        self.assertTrue(any("20 MB" in t for t in to_owner))
        self.assertTrue(any("Recordatorio" in t for t in to_owner), "no repartió el buzón")
        self.assertFalse(any(TOKEN in line for line in logs), "el token apareció en el registro")


if __name__ == "__main__":
    unittest.main()
