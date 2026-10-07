"""Pool de nube y enrutador sin red real (httpx.MockTransport).

    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_cloud
"""

import asyncio
import json
import logging
import time
import unittest

import httpx

from providers.base import LLMProvider
from providers.cloud import CloudPool, CloudProvider, KeyState
from providers.router import Router

SECRET_PROMPT = "dato-secreto-del-prompt"


class FakeLocal(LLMProvider):
    def __init__(self):
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return "respuesta local"


class FakeCloud:
    """Responde según la key: 'k429' -> 429 con Retry-After, 'k500' -> 500, 'k401' -> 401."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        host = request.url.host
        self.calls.append((host, key))
        if key == "k429":
            return httpx.Response(429, headers={"retry-after": "120"}, json={"error": SECRET_PROMPT})
        if key == "k500":
            return httpx.Response(500, json={})
        if key == "k401":
            return httpx.Response(401, json={})
        body = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": f"ok {host} {body['model']}"}}]})


def _provider(name, keys):
    return CloudProvider(name, f"modelo-{name}", f"https://{name}.test/v1", [KeyState(f"key#{i}", k) for i, k in enumerate(keys, 1)])


def _router(providers, cloud):
    local = FakeLocal()
    pool = CloudPool(providers, transport=httpx.MockTransport(cloud))
    return Router(local, "qwen-local", pool, []), local


MSG = [{"role": "user", "content": SECRET_PROMPT}]


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.logs: list[str] = []

        class Capture(logging.Handler):
            def emit(h, record):
                self.logs.append(h.format(record))

        self.handler = Capture()
        logging.getLogger().addHandler(self.handler)
        logging.getLogger().setLevel(logging.DEBUG)

    def tearDown(self):
        logging.getLogger().removeHandler(self.handler)

    def test_rotates_key_on_429_and_respects_retry_after(self):
        cloud = FakeCloud()
        p = _provider("groq", ["k429", "kbuena"])
        router, local = _router([p], cloud)
        text, info = asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertEqual((info["proveedor"], info["key"], info["nube"]), ("groq", "key#2", True))
        self.assertGreaterEqual(p.keys[0].cooldown_until - time.monotonic(), 110)
        self.assertEqual(local.calls, 0)

    def test_next_provider_then_local(self):
        cloud = FakeCloud()
        router, local = _router([_provider("groq", ["k500"]), _provider("gemini", ["kbuena"])], cloud)
        _, info = asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertEqual(info["proveedor"], "gemini")
        # todas en enfriamiento -> local, sin romperse
        router2, local2 = _router([_provider("groq", ["k500", "k401"])], cloud)
        text, info = asyncio.run(router2.chat_ex(MSG, cloud=True))
        self.assertEqual((text, info["nube"]), ("respuesta local", False))
        self.assertIn("nube no disponible", info["motivo_local"])
        # la key rechazada se enfría una hora
        self.assertGreater(router2.pool.providers[0].keys[1].cooldown_until - time.monotonic(), 3000)
        # segunda llamada: no reintenta keys en enfriamiento
        before = len(cloud.calls)
        asyncio.run(router2.chat_ex(MSG, cloud=True))
        self.assertEqual(len(cloud.calls), before)

    def test_private_never_goes_to_cloud(self):
        cloud = FakeCloud()
        router, local = _router([_provider("groq", ["kbuena"])], cloud)
        _, info = asyncio.run(router.chat_ex(MSG, cloud=True, private=True))
        self.assertEqual(cloud.calls, [])
        self.assertEqual((info["nube"], info["motivo_local"]), (False, "contenido privado"))
        asyncio.run(router.chat_ex(MSG))  # sin cloud=True (recordatorios, resumen): siempre local
        self.assertEqual(cloud.calls, [])
        self.assertEqual(local.calls, 2)

    def test_no_secrets_in_status_or_logs(self):
        cloud = FakeCloud()
        router, _ = _router([_provider("groq", ["k429", "k500", "kbuena"])], cloud)
        asyncio.run(router.chat_ex(MSG, cloud=True))
        dump = json.dumps(router.status()) + "\n".join(self.logs)
        for secret in ("k429", "k500", "kbuena", SECRET_PROMPT):
            self.assertNotIn(secret, dump)
        self.assertIn("key#1", dump)


if __name__ == "__main__":
    unittest.main()
