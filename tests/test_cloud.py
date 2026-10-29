"""Pool de nube y enrutador sin red real (httpx.MockTransport).

    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_cloud
"""

import asyncio
import json
import logging
import time
import unittest
from unittest import mock

import httpx

from config import settings
from providers.base import ChatUnavailable, LLMProvider
from providers.cloud import CloudPool, CloudProvider, KeyState
from providers.router import Router

SECRET_PROMPT = "dato-secreto-del-prompt"


class FakeLocal(LLMProvider):
    def __init__(self, up: bool = True):
        self.calls = 0
        self.up = up

    async def chat(self, messages):
        self.calls += 1
        if not self.up:  # Ollama sin modelo de chat (VPS en modo cloud)
            raise httpx.HTTPStatusError("404", request=httpx.Request("POST", "http://ollama"), response=httpx.Response(404))
        return "respuesta local"


class FakeLedger:
    def __init__(self, spent: float = 0.0):
        self.rows: list[dict] = []
        self.spent = spent

    def record(self, info):
        self.rows.append(info)

    def spent_today(self):
        return self.spent


class FakeCloud:
    """Responde según la key: 'k429' -> 429 con Retry-After, 'k500' -> 500, 'k401' -> 401,
    'k402' -> sin saldo, 'k429corto' -> 429 sin Retry-After las primeras 2 veces."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []

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
        if key == "k402":
            return httpx.Response(402, json={"error": SECRET_PROMPT})
        if key == "k429corto" and sum(1 for c in self.calls if c[1] == key) <= 2:
            return httpx.Response(429, json={})
        body = json.loads(request.content)
        self.bodies.append(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"ok {host} {body['model']}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.000013},
        })


def _provider(name, keys):
    return CloudProvider(name, f"modelo-{name}", f"https://{name}.test/v1", [KeyState(f"key#{i}", k) for i, k in enumerate(keys, 1)])


def _router(providers, cloud, local_up=True, ledger=None):
    local = FakeLocal(local_up)
    pool = CloudPool(providers, transport=httpx.MockTransport(cloud))
    return Router(local, "qwen-local", pool, [], ledger), local


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
        # Sin esperas reales entre reintentos; respaldo local encendido salvo que el test diga
        self.patches = [
            mock.patch.object(settings, "CLOUD_BACKOFF", 0),
            mock.patch.object(settings, "LOCAL_FALLBACK", True),
            mock.patch.object(settings, "DAILY_BUDGET_USD", None),
        ]
        for pt in self.patches:
            pt.start()

    def tearDown(self):
        logging.getLogger().removeHandler(self.handler)
        for pt in self.patches:
            pt.stop()

    def test_retries_429_with_backoff_then_answers(self):
        cloud = FakeCloud()
        ledger = FakeLedger()
        router, local = _router([_provider("openrouter", ["k429corto"])], cloud, ledger=ledger)
        text, info = asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertEqual(text, "ok openrouter.test modelo-openrouter")
        self.assertEqual(len(cloud.calls), 3)  # 2 fallos + 1 acierto, la misma key
        self.assertEqual(local.calls, 0)
        # OpenRouter: se pide el costo y se registra el uso
        self.assertEqual(cloud.bodies[0]["usage"], {"include": True})
        self.assertEqual((info["tokens_entrada"], info["tokens_salida"], info["costo_usd"]), (100, 20, 0.000013))
        self.assertEqual(ledger.rows[0]["proveedor"], "openrouter")

    def test_no_credit_without_fallback_gives_clear_notice(self):
        cloud = FakeCloud()
        router, local = _router([_provider("openrouter", ["k402"])], cloud)
        with mock.patch.object(settings, "LOCAL_FALLBACK", False):
            with self.assertRaises(ChatUnavailable) as ctx:
                asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertIn("saldo", str(ctx.exception))
        self.assertIn("NEXUS_LOCAL_FALLBACK=off", str(ctx.exception))
        self.assertEqual((len(cloud.calls), local.calls), (1, 0))  # 402 no se reintenta

    def test_no_credit_with_fallback_uses_local(self):
        router, local = _router([_provider("openrouter", ["k402"])], FakeCloud())
        text, info = asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertEqual((text, info["nube"], local.calls), ("respuesta local", False, 1))

    def test_budget_stops_spending(self):
        cloud = FakeCloud()
        router, local = _router([_provider("openrouter", ["kbuena"])], cloud, ledger=FakeLedger(spent=0.5))
        with mock.patch.object(settings, "DAILY_BUDGET_USD", 0.5):
            text, info = asyncio.run(router.chat_ex(MSG, cloud=True))
            self.assertEqual(cloud.calls, [])
            self.assertIn("presupuesto", info["motivo_local"])
            with mock.patch.object(settings, "LOCAL_FALLBACK", False):
                with self.assertRaises(ChatUnavailable) as ctx:
                    asyncio.run(router.chat_ex(MSG, cloud=True))
        self.assertIn("presupuesto diario", str(ctx.exception))
        self.assertEqual(cloud.calls, [])

    def test_private_without_local_model_is_not_sent(self):
        cloud = FakeCloud()
        router, _ = _router([_provider("openrouter", ["kbuena"])], cloud, local_up=False)
        with self.assertRaises(ChatUnavailable) as ctx:
            asyncio.run(router.chat_ex(MSG, cloud=True, private=True))
        self.assertIn("No lo envié a la nube", str(ctx.exception))
        self.assertEqual(cloud.calls, [])

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
