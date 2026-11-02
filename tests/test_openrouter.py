"""Cliente único de OpenRouter sin red real (httpx.MockTransport).

    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_openrouter
"""

import asyncio
import json
import logging
import unittest
from unittest import mock

import httpx

from providers import openrouter
from providers.openrouter import ChatUnavailable, OpenRouter

KEY = "clave-secreta-de-prueba"
SECRET_PROMPT = "dato-secreto-del-prompt"
MSG = [{"role": "user", "content": SECRET_PROMPT}]
OK = {
    "model": "deepseek/deepseek-v4-flash",
    "choices": [{"message": {"content": "hola"}}],
    "usage": {"prompt_tokens": 9, "completion_tokens": 2, "cost": 0.00001},
}


class FakeLedger:
    def __init__(self, spent: float = 0.0):
        self.rows: list[dict] = []
        self.spent = spent
        self.alerts: list[str] = []
        self.armed: set[str] = set()

    def record(self, info):
        self.rows.append(info)

    def spent_today(self):
        return self.spent

    def alert_once(self, key, text):  # como core/usage.py: una vez hasta clear_alert
        if key not in self.armed:
            self.armed.add(key)
            self.alerts.append(text)

    def clear_alert(self, key):
        self.armed.discard(key)


class Script:
    """Devuelve las respuestas en orden (la última se repite) y guarda los cuerpos enviados."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):  # copia: una respuesta no se puede leer dos veces
            return httpx.Response(r.status_code, headers=r.headers, content=r.content)
        return r()


def ok():
    return httpx.Response(200, json=OK)


def client(script, ledger=None, budget=None, key=KEY):
    return OpenRouter(key, "deepseek/deepseek-v4-flash", budget, ledger or FakeLedger(), httpx.MockTransport(script))


def run(c, msgs=MSG):
    return asyncio.run(c.chat_ex(msgs))


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(openrouter, "BACKOFF", 0.01)
        p.start()
        self.addCleanup(p.stop)


class TestOpenRouter(Base):
    def test_ok_registra_uso_y_politica_de_datos(self):
        s, ledger = Script(ok), FakeLedger()
        text, info = run(client(s, ledger))
        self.assertEqual(text, "hola")
        self.assertEqual(info["costo_usd"], 0.00001)
        self.assertEqual(ledger.rows[0]["tokens_entrada"], 9)
        body = s.bodies[0]
        self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual(body["usage"], {"include": True})
        self.assertEqual(body["temperature"], 0)

    def test_reintenta_5xx_y_red_con_espera_creciente(self):
        s = Script(httpx.Response(500), httpx.ConnectError("x"), ok)
        with mock.patch.object(openrouter.asyncio, "sleep", wraps=asyncio.sleep) as sleep:
            text, _ = run(client(s))
        self.assertEqual(text, "hola")
        self.assertEqual(len(s.bodies), 3)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(waits, [0.01, 0.02])

    def test_429_persistente_da_aviso_claro(self):
        s = Script(httpx.Response(429))
        with self.assertRaises(ChatUnavailable) as ctx:
            run(client(s))
        self.assertEqual(ctx.exception.reason, "limite")
        self.assertIn("limitando", str(ctx.exception))
        self.assertEqual(len(s.bodies), 1 + openrouter.RETRIES)

    def test_retry_after_largo_no_se_espera(self):
        s = Script(httpx.Response(429, headers={"retry-after": "120"}))
        with self.assertRaises(ChatUnavailable):
            run(client(s))
        self.assertEqual(len(s.bodies), 1)

    def test_402_no_reintenta_y_avisa_una_sola_vez(self):
        s, ledger = Script(httpx.Response(402)), FakeLedger()
        c = client(s, ledger)
        for _ in range(3):
            with self.assertRaises(ChatUnavailable) as ctx:
                run(c)
            self.assertEqual(ctx.exception.reason, "sin_saldo")
            self.assertIn("sin saldo", str(ctx.exception))
        self.assertEqual(len(s.bodies), 3)  # sin reintentos
        self.assertEqual(len(ledger.alerts), 1)
        # Recargó: al primer éxito el aviso se rearma para la próxima vez
        s.responses = [ok]
        run(c)
        s.responses = [httpx.Response(402)]
        with self.assertRaises(ChatUnavailable):
            run(c)
        self.assertEqual(len(ledger.alerts), 2)

    def test_presupuesto_alcanzado_avisa_y_no_llama(self):
        s = Script(ok)
        with self.assertRaises(ChatUnavailable) as ctx:
            run(client(s, FakeLedger(spent=0.5), budget=0.5))
        self.assertEqual(ctx.exception.reason, "presupuesto")
        self.assertIn("Llegué al tope de hoy", str(ctx.exception))
        self.assertEqual(s.bodies, [])

    def test_sin_key_no_llama(self):
        s = Script(ok)
        with self.assertRaises(ChatUnavailable) as ctx:
            run(client(s, key=""))
        self.assertEqual(ctx.exception.reason, "sin_config")
        self.assertEqual(s.bodies, [])

    def test_pausa_creciente_y_se_limpia_al_responder(self):
        s = Script(httpx.Response(401))
        c = client(s)
        with self.assertRaises(ChatUnavailable) as ctx:
            run(c)
        self.assertEqual(ctx.exception.reason, "clave")
        first = c.paused_for()
        self.assertGreater(first, openrouter.PAUSE_MIN - 2)
        with self.assertRaises(ChatUnavailable):
            run(c)
        self.assertGreater(c.paused_for(), first + openrouter.PAUSE_MIN - 2)
        s.responses = [ok]
        run(c)
        self.assertEqual(c.paused_for(), 0)

    def test_formato_inesperado_es_error_claro(self):
        s = Script(httpx.Response(200, json={"nada": 1}))
        with self.assertRaises(ChatUnavailable) as ctx:
            run(client(s))
        self.assertEqual(ctx.exception.reason, "caido")

    def test_nunca_registra_key_ni_prompt(self):
        s = Script(httpx.Response(500, json={"error": SECRET_PROMPT}))
        with self.assertLogs("nexus.llm", logging.DEBUG) as logs, self.assertRaises(ChatUnavailable) as ctx:
            run(client(s))
        text = "\n".join(logs.output) + str(ctx.exception)
        self.assertNotIn(KEY, text)
        self.assertNotIn(SECRET_PROMPT, text)


if __name__ == "__main__":
    unittest.main()
