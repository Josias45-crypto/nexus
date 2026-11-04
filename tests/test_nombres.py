"""El jefe es Josias: Isabelle nunca inventa un nombre de persona («señor Joaquín»).

    docker run --rm -e NEXUS_DATA_DIR=/tmp/n -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_nombres
"""

import asyncio
import unittest
from datetime import datetime
from unittest import mock

from core import charla, conversa, db, iniciativa, nombres, persona


class IA:
    """Devuelve las respuestas en orden y guarda lo que le pidieron."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.pedidos: list[list[dict]] = []
        self.model, self.deep_model = "m", ""

    async def chat_ex(self, messages, **kw):
        self.pedidos.append(messages)
        return self.respuestas.pop(0), {}


class DetectorTest(unittest.TestCase):
    def test_joaquin_en_todas_sus_posiciones(self):
        for texto in ("¡Hola, señor Joaquín! Qué bueno verlo.", "Hola, Joaquín, ¿cómo va todo?", "Joaquín, buenos días.",
                      "Buenas noches, ingeniero Joaquín.", "Ya le avisé a Joaquín."):
            with self.subTest(texto=texto):
                self.assertEqual(nombres.inventados(texto, []), ["Joaquín"])

    def test_lo_permitido(self):
        for texto, fuentes in [("¡Hola, señor Josias!", []), ("Listo, ingeniero Josias.", []), ("Gracias, gerente!", []),
                               ("Soy Isabelle, jefe.", []), ("Rosa Quispe paga el viernes.", ["Rosa Quispe paga los viernes"]),
                               ("Publique en Instagram y TikTok, jefe.", []), ("El señor que vino ayer pagó.", []),
                               ("Hola, Joaquín.", ["mi contador se llama Joaquín"])]:
            with self.subTest(texto=texto):
                self.assertEqual(nombres.inventados(texto, fuentes), [])

    def test_la_personalidad_sabe_su_nombre(self):
        persona.get.cache_clear()
        self.assertEqual(persona.nombre_jefe(), "Josias")
        self.assertIn("se llama Josias", persona.sistema())
        self.assertIn("jamás otro nombre", persona.sistema())


class CharlaTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            c.execute("DELETE FROM charla")
            c.execute("DELETE FROM kv")
        persona.get.cache_clear()
        persona.reiniciar()
        p = mock.patch.object(conversa, "search", self._nada)
        p.start()
        self.addCleanup(p.stop)

    async def _nada(self, *a, **kw):
        return []

    def hablar(self, ia: IA, texto: str = "hola Isabelle") -> conversa.Respuesta:
        charla.agregar("jefe", texto, "1")
        with mock.patch.object(conversa, "get_client", lambda: ia):
            return asyncio.run(conversa._hablar("charlar", texto, "1", False))

    def test_regenera_una_vez_sin_el_nombre_inventado(self):
        ia = IA("¡Hola, señor Joaquín! Qué bueno verlo por acá.", "¡Hola, jefe! Qué bueno verlo por acá.")
        r = self.hablar(ia)
        self.assertEqual(r.texto, "¡Hola, jefe! Qué bueno verlo por acá.")
        self.assertEqual(len(ia.pedidos), 2)
        self.assertIn("No uses Joaquín", ia.pedidos[1][-1]["content"])
        self.assertIn("Josias", ia.pedidos[1][-1]["content"])

    def test_si_insiste_usa_el_respaldo(self):
        r = self.hablar(IA("¡Hola, señor Joaquín!", "Hola, Joaquín, ¿todo bien?"))
        self.assertNotIn("Joaquín", r.texto)
        self.assertRegex(r.texto, r"Aquí estoy|aquí lo escucho")

    def test_un_nombre_que_el_jefe_dijo_vale(self):
        ia = IA("Claro, a Joaquín le escribo el recordatorio cuando me diga.")
        r = self.hablar(ia, "mi contador se llama Joaquín y viene el lunes")
        self.assertIn("Joaquín", r.texto)
        self.assertEqual(len(ia.pedidos), 1)


class SaludoTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        persona.get.cache_clear()

    def test_verificar_rechaza_joaquin(self):
        self.assertEqual(iniciativa.verificar("Buenos días, señor Joaquín. ¿Cómo amaneció?", "manana", "Hoy no tiene recordatorios.", []),
                         "nombre Joaquín")

    def redactar(self, ia: IA) -> dict:
        with mock.patch.object(iniciativa, "get_client", lambda: ia):
            return asyncio.run(iniciativa.redactar("manana", datetime(2026, 11, 4, 7, 30)))

    def test_saludo_regenera_y_luego_respaldo(self):
        ia = IA("Buenos días, señor Joaquín. ¿Cómo amaneció?", "Buenos días, jefe. ¿Cómo amaneció hoy?")
        r = self.redactar(ia)
        self.assertEqual((r["fuente"], r["texto"]), ("ia", "Buenos días, jefe. ¿Cómo amaneció hoy?"))
        self.assertEqual(len(ia.pedidos), 2)
        r = self.redactar(IA("Buenos días, señor Joaquín.", "¡Hola, Joaquín! ¿Qué tal?"))
        self.assertEqual(r["fuente"], "respaldo")
        self.assertNotIn("Joaquín", r["texto"])


if __name__ == "__main__":
    unittest.main()
