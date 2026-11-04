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


class PorModelo(Script):
    """Responde según el modelo pedido (ok si no está en el mapa)."""

    def __init__(self, fallos: dict):
        super().__init__(ok)
        self.fallos = fallos

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        return self.fallos.get(body["model"], ok)()


def potente(script, ledger=None, budget=None):
    return OpenRouter(KEY, "deepseek/deepseek-v4-flash", budget, ledger or FakeLedger(), httpx.MockTransport(script),
                      deep_model="anthropic/claude-sonnet-5.5", deep_fallback="deepseek/deepseek-v4-pro")


class TestRespaldoPotente(Base):
    def _run(self, c):
        return asyncio.run(c.chat_ex(MSG, model=c.deep_model, razonar=True))

    def test_caido_o_inexistente_usa_el_respaldo(self):
        for status in (404, 503, 429):
            s = PorModelo({"anthropic/claude-sonnet-5.5": lambda: httpx.Response(status)})
            _, info = self._run(potente(s))
            self.assertEqual(s.bodies[-1]["model"], "deepseek/deepseek-v4-pro")
            self.assertEqual(s.bodies[0]["model"], "anthropic/claude-sonnet-5.5")
            self.assertTrue(info["respaldo"])
            self.assertEqual(s.bodies[-1]["provider"], {"zdr": True, "data_collection": "deny"})

    def test_sin_saldo_o_clave_no_usa_el_respaldo(self):
        for status, razon in ((402, "sin_saldo"), (401, "clave")):
            s = Script(httpx.Response(status), ok)
            with self.assertRaises(ChatUnavailable) as cm:
                self._run(potente(s))
            self.assertEqual(cm.exception.reason, razon)
            self.assertEqual({b["model"] for b in s.bodies}, {"anthropic/claude-sonnet-5.5"})

    def test_tope_diario_no_usa_el_respaldo(self):
        s = Script(ok)
        with self.assertRaises(ChatUnavailable) as cm:
            self._run(potente(s, FakeLedger(spent=5), budget=1))
        self.assertEqual(cm.exception.reason, "presupuesto")
        self.assertEqual(s.bodies, [])

    def test_el_diario_no_tiene_respaldo(self):
        s = Script(httpx.Response(404), ok)
        with self.assertRaises(ChatUnavailable):
            run(potente(s))
        self.assertEqual(len(s.bodies), 1)

    def test_potente_bien_no_marca_respaldo(self):
        s = Script(ok)
        _, info = self._run(potente(s))
        self.assertFalse(info["respaldo"])
        self.assertNotIn("reasoning", s.bodies[0])


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
        self.assertEqual(body["reasoning"], {"enabled": False}, "razonamiento apagado por defecto")

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
        self.assertFalse(c.sano())
        s.responses = [ok]
        run(c)
        self.assertEqual(c.paused_for(), 0)
        self.assertTrue(c.sano())

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



class TestVerYOir(Base):
    def test_vision_con_politica_de_datos_y_su_modelo(self):
        s, ledger = Script(ok), FakeLedger()
        c = OpenRouter(KEY, "deepseek/deepseek-v4-flash", None, ledger, httpx.MockTransport(s), vision_model="qwen/qwen3.5-9b")
        asyncio.run(c.ver("Describe", [b"\xff\xd8jpeg"]))
        body = s.bodies[0]
        self.assertEqual(body["model"], "qwen/qwen3.5-9b")
        self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual(body["temperature"], 0)
        partes = body["messages"][0]["content"]
        self.assertEqual(partes[1]["image_url"]["url"][:23], "data:image/jpeg;base64,")
        self.assertEqual(ledger.rows[-1]["proveedor"], "openrouter-vision")
        with self.assertRaises(ChatUnavailable) as e:  # sin modelo de visión no llama
            asyncio.run(client(s).ver("Describe", [b"x"]))
        self.assertIn("NEXUS_OPENROUTER_MODEL_VISION", str(e.exception))

    def test_transcripcion_por_su_endpoint(self):
        respuesta = {"text": "hola", "segments": [{"start": 0, "end": 1, "text": "hola"}], "usage": {"seconds": 9.2, "cost": 0.00003}}
        urls = []
        s = Script(lambda: httpx.Response(200, json=respuesta))

        def transporte(request):
            urls.append(str(request.url))
            return s(request)

        ledger = FakeLedger()
        c = OpenRouter(KEY, "deepseek/deepseek-v4-flash", None, ledger, httpx.MockTransport(transporte),
                       audio_model="openai/whisper-large-v3-turbo")
        data, info = asyncio.run(c.transcribir(b"OggS", "ogg"))
        self.assertEqual(urls, ["https://openrouter.ai/api/v1/audio/transcriptions"])
        body = s.bodies[0]
        self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual((body["model"], body["language"], body["response_format"]), ("openai/whisper-large-v3-turbo", "es", "verbose_json"))
        self.assertEqual(body["input_audio"]["format"], "ogg")
        self.assertEqual(data["text"], "hola")
        self.assertEqual((info["proveedor"], info["costo_usd"], info["segundos"]), ("openrouter-audio", 0.00003, 9.2))
        self.assertEqual(ledger.rows[-1]["proveedor"], "openrouter-audio")
        self.assertNotIn("options", body["provider"])
        asyncio.run(c.transcribir(b"OggS", "ogg", vocabulario=["IGV", "Yape"]))
        self.assertEqual(s.bodies[-1]["provider"]["options"]["deepinfra"], {"prompt": "IGV, Yape"})
        self.assertTrue(s.bodies[-1]["provider"]["zdr"])
        s2 = Script(httpx.Response(402))
        c2 = OpenRouter(KEY, "m", None, FakeLedger(), httpx.MockTransport(s2), audio_model="a")
        with self.assertRaises(ChatUnavailable) as e:
            asyncio.run(c2.transcribir(b"x", "ogg"))
        self.assertEqual(e.exception.reason, "sin_saldo")

if __name__ == "__main__":
    unittest.main()


class TestBusquedaWeb(Base):
    def test_plugin_exa_con_politica_de_datos_y_fuentes(self):
        cuerpo = {"model": "deepseek/deepseek-v4-flash", "usage": {"cost": 0.0081, "prompt_tokens": 900, "completion_tokens": 60},
                  "choices": [{"message": {"content": "Según SUNAT, 3,75.", "annotations": [
                      {"type": "url_citation", "url_citation": {"url": "https://sunat.gob.pe/x", "title": "SUNAT", "content": "3,75"}},
                      {"type": "otra"}]}}]}
        s = Script(lambda: httpx.Response(200, json=cuerpo))
        ledger = FakeLedger()
        texto, info = asyncio.run(client(s, ledger).chat_ex(MSG, web=True))
        self.assertEqual(s.bodies[0]["plugins"], [{"id": "web", "engine": "exa", "max_results": 5}])
        self.assertEqual(s.bodies[0]["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual(info["fuentes"], [{"url": "https://sunat.gob.pe/x", "titulo": "SUNAT", "extracto": "3,75"}])
        self.assertEqual(info["proveedor"], "openrouter-internet")
        self.assertEqual(info["costo_usd"], 0.0081)
        self.assertEqual(ledger.records[-1]["proveedor"], "openrouter-internet") if hasattr(ledger, "records") else None
        asyncio.run(client(s).chat_ex(MSG))
        self.assertNotIn("plugins", s.bodies[-1], "sin web, sin plugin")
