"""Conversación en lenguaje natural sin red: reglas baratas, intérprete con validación, acciones,
niveles de respuesta y contexto acotado. La IA es un doble (FakeLLM).

    docker run --rm -e NEXUS_DATA_DIR=/tmp/c -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_conversa
"""

import asyncio
import json
import logging
import unittest
from pathlib import Path
from unittest import mock

from core import ask, charla, conversa, db, persona
from providers.openrouter import ChatUnavailable

CHAT, CANAL = "1001", "simulado"


class FakeLLM:
    """Responde en orden; registra cada llamada (mensajes y opciones)."""

    model, deep_model = "diario", "potente"

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.calls: list[dict] = []

    async def chat_ex(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        r = self.respuestas.pop(0) if self.respuestas else "Claro, jefe."
        if isinstance(r, Exception):
            raise r
        return r, {"proveedor": "openrouter", "modelo": kw.get("model") or self.model, "costo_usd": 0.0}

    async def chat(self, messages, **kw):
        return (await self.chat_ex(messages, **kw))[0]


def notas() -> list[str]:
    with db.connect() as c:
        rows = c.execute("SELECT raw_path FROM events WHERE kind = 'text' ORDER BY created_at").fetchall()
    return [Path(r["raw_path"]).read_text(encoding="utf-8") for r in rows]


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("charla", "reminders", "pending_confirmations", "chunks", "events"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM kv")
        persona.get.cache_clear()
        self.llm = FakeLLM()
        for target, value in (("core.conversa.get_client", lambda: self.llm),
                              ("core.conversa.search", self._sin_busqueda)):
            p = mock.patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    async def _sin_busqueda(*a, **kw):
        return []

    def turno(self, *textos) -> dict:
        return asyncio.run(conversa.conversar(list(textos), CANAL, CHAT))


class ReglasTest(Base):
    def test_cierre_sin_ia_ni_pregunta(self):
        for t in ("ok ok", "gracias", "Ok", "chau", "listo gracias", "me voy a dormir, hasta mañana"):
            with self.subTest(t=t):
                r = self.turno(t)
                self.assertEqual(r["accion"], "cierre")
                self.assertNotIn("?", r["texto"])
        self.assertEqual(self.llm.calls, [])
        frases = persona.get()["frases"]
        self.assertIn(self.turno("me voy a dormir, hasta mañana")["texto"].split(",")[0],
                      [f.split(",")[0].split(";")[0] for f in frases["noche"]])
        self.assertNotIn("De nada", self.turno("ok ok")["texto"])

    def test_recuerda_esto_guarda_tal_cual(self):
        r = self.turno("Recuerda esto: Rosa paga los viernes en efectivo")
        self.assertEqual(r["accion"], "guardar")
        self.assertEqual(notas(), ["Rosa paga los viernes en efectivo"])
        self.assertEqual(self.llm.calls, [])

    def test_recuerda_esto_solo_guarda_lo_anterior(self):
        self.llm.respuestas = ['{"accion": "charlar", "consulta": ""}', "Qué bueno, jefe."]
        self.turno("El local nuevo abre a las 10")
        self.turno("recuerda esto")
        self.assertEqual(notas(), ["El local nuevo abre a las 10"])

    def test_ayuda_y_lista_sin_ia(self):
        self.assertIn("sin comandos", self.turno("¿qué puedes hacer?")["texto"])
        self.assertIn("No tiene recordatorios", self.turno("¿qué recordatorios tengo?")["texto"])
        self.assertEqual(self.llm.calls, [])

    def test_pregunta_por_fecha_sin_interprete(self):
        async def fake_ask(q, k=None):
            return {"answer": "Hoy aprendí 1 cosa(s):\n• 10:00 nota.txt: algo", "sources": [], "llm": None, "tipo": "temporal"}

        with mock.patch.object(ask, "ask", fake_ask):
            r = self.turno("¿qué aprendí hoy?")
        self.assertEqual((r["accion"], self.llm.calls), ("preguntar", []))
        self.assertIn("Hoy aprendí", r["texto"])

    def test_saludo_va_directo_a_charla(self):
        r = self.turno("hola")
        self.assertEqual(r["accion"], "charlar")
        self.assertEqual(len(self.llm.calls), 1, "sin intérprete")
        self.assertEqual(self.llm.calls[0]["temperature"], 0.8)
        self.assertIsNone(self.llm.calls[0].get("model"), "charla con el modelo diario")

    def test_profundo_usa_el_modelo_potente_y_avisa(self):
        r = self.turno("analiza a fondo si me conviene abrir un local en el centro")
        call = self.llm.calls[0]
        self.assertEqual((r["accion"], call["model"], call["temperature"]), ("analizar", "potente", 0.3))
        self.assertTrue(call["razonar"], "a fondo puede razonar")
        self.assertTrue(r["texto"].startswith(persona.get()["frases"]["profundo"].split("{")[0]))
        r = self.turno("dame tu opinión sobre mi nuevo logo")
        self.assertEqual((r["accion"], self.llm.calls[-1]["temperature"]), ("opinar", 0.8))
        guardado = [m["texto"] for m in charla.recientes() if m["rol"] == "isabelle"]
        self.assertFalse(any("modelo potente" in t for t in guardado), "el aviso no entra al historial")

    def test_si_la_ia_imita_el_aviso_se_quita(self):
        aviso = persona.frase("profundo")
        self.llm.respuestas = ['{"accion": "charlar", "consulta": ""}', f"{aviso}\n\nQué bueno, jefe."]
        r = self.turno("hoy cerré una venta grande con un cliente nuevo")
        self.assertEqual(r["texto"], "Qué bueno, jefe.")


class InterpreteTest(Base):
    def test_validar(self):
        self.assertEqual(conversa.validar({"accion": "opinar"}), {"accion": "opinar", "consulta": ""})
        for malo in ({"accion": "borrar_todo"}, {"accion": "guardar", "consulta": 5}, [], "texto"):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                conversa.validar(malo)

    def test_json_roto_dos_veces_no_ejecuta_nada(self):
        self.llm.respuestas = ["no es json", '{"accion": "borrar_todo"}']
        r = self.turno("haz lo de siempre con lo otro")
        self.assertEqual(r["accion"], "no_entendi")
        self.assertEqual(len(self.llm.calls), 2, "un reintento y nada más")
        self.assertEqual(notas(), [])

    def test_json_roto_y_luego_valido(self):
        self.llm.respuestas = ["{roto", '{"accion": "charlar", "consulta": ""}', "Aquí estoy, jefe."]
        r = self.turno("me fue bien en la reunión de hoy")
        self.assertEqual((r["accion"], r["texto"]), ("charlar", "Aquí estoy, jefe."))

    def test_guardar_guarda_las_palabras_del_jefe_y_comenta(self):
        self.llm.respuestas = ['{"accion": "guardar", "consulta": "TEXTO INVENTADO POR LA IA"}', "Anotado, jefe."]
        r = self.turno("El proveedor Tralvex entrega los martes")
        self.assertEqual(notas(), ["El proveedor Tralvex entrega los martes"])
        self.assertEqual((r["accion"], r["texto"]), ("guardar", "Anotado, jefe."))
        self.assertIn("Ya guardaste", self.llm.calls[-1]["messages"][0]["content"])


class NivelesTest(Base):
    def test_memoria_con_fuentes(self):
        self.llm.respuestas = ['{"accion": "preguntar", "consulta": "¿Cuánto debe Rosa?"}']

        async def fake_ask(q, k=None):
            return {"answer": "**Rosa** debe 120 soles.", "sources": [{"filename": "ventas.xlsx", "cita": "ventas.xlsx, fila 3"}],
                    "llm": {}, "tipo": "memoria"}

        with mock.patch.object(ask, "ask", fake_ask):
            r = self.turno("¿cuánto me debe Rosa?")
        self.assertEqual(r["nivel"], "memoria")
        self.assertEqual(r["texto"], "Rosa debe 120 soles.\n\nFuentes: ventas.xlsx, fila 3")

    def test_general_avisa_que_no_sale_de_la_memoria(self):
        self.llm.respuestas = ['{"accion": "preguntar", "consulta": "¿Qué es el ROI?"}', "Es el retorno de la inversión."]

        async def no_sabe(q, k=None):
            return {"answer": ask.NO_INFO, "sources": [], "llm": None, "tipo": "memoria"}

        with mock.patch.object(ask, "ask", no_sabe):
            r = self.turno("¿qué es el ROI?")
        self.assertEqual(r["nivel"], "general")
        self.assertIn("no sale de mi memoria", r["texto"])
        self.assertTrue(r["texto"].endswith("Es el retorno de la inversión."))

    def test_dato_personal_que_no_tiene_no_se_inventa(self):
        self.llm.respuestas = ['{"accion": "preguntar", "consulta": "¿Cuánto me debe Rosa?"}',
                               "NO_ANOTADO No lo tengo anotado, jefe. ¿Me lo cuenta?"]

        async def no_sabe(q, k=None):
            return {"answer": ask.NO_INFO, "sources": [], "llm": None, "tipo": "memoria"}

        with mock.patch.object(ask, "ask", no_sabe):
            r = self.turno("¿cuánto me debe Rosa?")
        self.assertIsNone(r["nivel"])
        self.assertEqual(r["texto"], "No lo tengo anotado, jefe. ¿Me lo cuenta?")

    def test_openrouter_caido_una_linea_y_no_se_pierde(self):
        self.llm.respuestas = [ChatUnavailable("caido", "⚠ No pude conectar con OpenRouter. Lo que me envíe queda guardado.")]
        r = self.turno("¿cómo va todo?")
        self.assertEqual(r["accion"], "aviso")
        self.assertNotIn("\n", r["texto"])
        self.assertEqual([m["texto"] for m in charla.recientes() if m["rol"] == "jefe"], ["¿cómo va todo?"])


class ContextoTest(Base):
    def test_contexto_acotado_y_ficha_separada(self):
        for i in range(30):
            charla.agregar("jefe" if i % 2 == 0 else "isabelle", f"mensaje {i}", CHAT)
        with db.connect() as c:
            c.execute("INSERT INTO kv (key, value) VALUES ('jefe:ficha', ?)",
                      (json.dumps({"personas": ["Piter: proveedor"]}),))
        hits = [{"content": f"nota {i}", "filename": f"n{i}.txt", "meta": {}, "distance": 0.3, "position": 0,
                 "event_id": str(i)} for i in range(6)]

        async def con_hits(*a, **kw):
            return hits

        self.llm.respuestas = ['{"accion": "charlar", "consulta": ""}', "Bien, jefe."]
        with mock.patch("core.conversa.search", con_hits):
            self.turno("hoy estuve pensando en el negocio nuevo")
        call = self.llm.calls[-1]
        sistema, resto = call["messages"][0]["content"], call["messages"][1:]
        self.assertLessEqual(len(resto), charla.RECIENTES, "nunca todo el historial")
        self.assertEqual(resto[-1]["content"], "hoy estuve pensando en el negocio nuevo")
        self.assertIn("[LO QUE SÉ DEL JEFE", sistema)
        self.assertIn("Piter: proveedor", sistema)
        self.assertEqual(sistema.count("De n"), conversa.FRAGMENTOS_MAX, "máx. 4 fragmentos")

    def test_historial_guardado_y_sin_contenido_en_logs(self):
        self.llm.respuestas = ['{"accion": "charlar", "consulta": ""}', "Qué alegría, jefe."]
        with self.assertLogs("nexus.conversa", logging.INFO) as logs:
            self.turno("vendí 12 laptops hoy")
        self.assertNotIn("laptops", "\n".join(logs.output))
        roles = [m["rol"] for m in charla.recientes()]
        self.assertEqual(roles, ["jefe", "isabelle"])


if __name__ == "__main__":
    unittest.main()
