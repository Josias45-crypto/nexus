"""Canal de WhatsApp sin llamar a Meta: firma del webhook, verificación y envío.

    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_whatsapp
"""

import asyncio
import hashlib
import hmac
import json
import unittest

import httpx
from fastapi.testclient import TestClient

from channels.base import PermanentSendError
from channels.whatsapp import WhatsAppChannel, build_app, valid_signature

SECRET = "app-secret-de-prueba"
VERIFY = "verificar-123"
PAYLOAD = {
    "entry": [{"changes": [{"value": {"messages": [
        {"from": "51999000111", "type": "text", "text": {"body": "hola"}},
        {"from": "51999000111", "type": "audio", "audio": {"id": "m1", "voice": True, "mime_type": "audio/ogg"}},
    ]}}]}]
}


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class WhatsAppTest(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def graph(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["to"] == "fuera":
                return httpx.Response(400, json={"error": {"code": 131047}})
            self.sent.append(body)
            return httpx.Response(200, json={"messages": [{"id": "x"}]})

        self.channel = WhatsAppChannel("tok", "123", ["51999000111"], "v21.0", httpx.MockTransport(graph))
        self.client = TestClient(build_app(self.channel, VERIFY, SECRET))

    def test_signature(self):
        self.assertTrue(valid_signature(SECRET, b"x", _sign(b"x")))
        self.assertFalse(valid_signature(SECRET, b"x", _sign(b"y")))
        self.assertFalse(valid_signature(SECRET, b"x", None))
        self.assertFalse(valid_signature("", b"x", _sign(b"x")))

    def test_verify_webhook(self):
        ok = self.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"})
        self.assertEqual((ok.status_code, ok.text), (200, "42"))
        bad = self.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"})
        self.assertEqual(bad.status_code, 403)

    def test_post_requires_signature(self):
        body = json.dumps(PAYLOAD).encode()
        self.assertEqual(self.client.post("/webhook", content=body).status_code, 403)
        self.assertEqual(self.channel.queue.qsize(), 0)
        r = self.client.post("/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.channel.queue.qsize(), 2)
        first = self.channel.queue.get_nowait()
        voice = self.channel.queue.get_nowait()
        self.assertEqual(first.text, "hola")
        self.assertTrue(voice.is_voice)

    def test_send_and_window(self):
        asyncio.run(self.channel.send("51999000111", "hola"))
        self.assertEqual(self.sent[0]["text"]["body"], "hola")
        with self.assertRaises(PermanentSendError):
            asyncio.run(self.channel.send("fuera", "hola"))


if __name__ == "__main__":
    unittest.main()
