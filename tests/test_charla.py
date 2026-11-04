"""Memoria de la charla sin red: historial con búsqueda, ficha del jefe validada campo por campo,
versión anterior si algo falla y actualización solo cuando nadie espera respuesta.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/m -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_charla
"""

import asyncio
import json
import unittest
from datetime import date
from unittest import mock

from core import charla, clock, db

BUENA = {"ficha": {**{k: [] for k in charla.CAMPOS}, "personas": ["Piter: proveedor de laptops"]},
         "resumen": "Hablaron de Piter."}


class FakeLLM:
    pausa = 0.0

    def paused_for(self):
        return self.pausa

    def sano(self):
        return not self.pausa

    def __init__(self, *respuestas, espera: float = 0):
        self.respuestas = list(respuestas)
        self.prompts: list[str] = []
        self.espera = espera

    async def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        await asyncio.sleep(self.espera)
        return self.respuestas.pop(0) if self.respuestas else json.dumps(BUENA)


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            c.execute("DELETE FROM charla")
            c.execute("DELETE FROM kv")

    def con(self, llm):
        p = mock.patch("providers.openrouter.get_client", lambda: llm)
        p.start()
        self.addCleanup(p.stop)
        return llm


class ValidarTest(unittest.TestCase):
    def test_ok_recorta_y_quita_repetidos(self):
        datos = {"ficha": {**{k: [] for k in charla.CAMPOS},
                           "metas": ["  abrir   local ", "Abrir local", *[f"meta {i}" for i in range(15)]]},
                 "resumen": " ".join(["palabra"] * 200) + "."}
        f, r = charla.validar(datos)
        self.assertEqual(len(f["metas"]), charla.LISTA_MAX)
        self.assertNotIn("Abrir local", f["metas"])
        self.assertLessEqual(len(r.split()), charla.RESUMEN_MAX)

    def test_errores(self):
        vacia = {k: [] for k in charla.CAMPOS}
        malos = [
            {"resumen": ""},
            {"ficha": vacia},
            {"ficha": {k: v for k, v in vacia.items() if k != "metas"}, "resumen": ""},
            {"ficha": {**vacia, "metas": "una"}, "resumen": ""},
            {"ficha": {**vacia, "metas": [True]}, "resumen": ""},
            {"ficha": {**vacia, "metas": [{"x": 1}]}, "resumen": ""},
        ]
        for m in malos:
            with self.subTest(m=m), self.assertRaises(ValueError):
                charla.validar(m)


class HistorialTest(Base):
    def test_busqueda_por_texto_y_fecha(self):
        charla.agregar("jefe", "Piter trae las laptops el lunes", "1")
        charla.agregar("isabelle", "Anotado, jefe.", "1")
        self.assertEqual([m["texto"] for m in charla.buscar("laptops")], ["Piter trae las laptops el lunes"])
        dia = clock.now_local().date()  # el día local, no el de UTC
        hoy = charla.buscar(desde=dia, hasta=dia)
        self.assertGreaterEqual(len(hoy), 1)
        self.assertEqual(charla.buscar("laptops", desde=date(2000, 1, 1), hasta=date(2000, 1, 2)), [])


class MemoriaTest(Base):
    def test_solo_lo_que_dice_el_jefe_es_dato(self):
        charla.agregar("jefe", "Piter es mi proveedor de laptops", "1")
        charla.agregar("isabelle", "¿Y Rosaura es su socia?", "1")
        llm = self.con(FakeLLM())
        self.assertTrue(asyncio.run(charla.actualizar()))
        prompt = llm.prompts[0]
        del_jefe = prompt.split("MENSAJES DEL JEFE")[1].split("CONVERSACIÓN")[0]
        self.assertIn("Piter es mi proveedor", del_jefe)
        self.assertNotIn("Rosaura", del_jefe, "lo que dice Isabelle no es dato")
        self.assertEqual(charla.ficha()["personas"], ["Piter: proveedor de laptops"])
        self.assertEqual(charla.resumen(), "Hablaron de Piter.")

    def test_si_falla_conserva_la_version_anterior(self):
        charla.agregar("jefe", "Piter es mi proveedor de laptops", "1")
        self.con(FakeLLM())
        asyncio.run(charla.actualizar())
        charla.agregar("jefe", "Ahora vendo también tablets", "1")
        self.con(FakeLLM("no es json", '{"ficha": {}, "resumen": ""}', "{roto"))
        for _ in range(charla.FALLOS_MAX):
            self.assertFalse(asyncio.run(charla.actualizar()))
            self.assertEqual(charla.ficha()["personas"], ["Piter: proveedor de laptops"])
        self.assertEqual(charla.pendientes(), [], "tras FALLOS_MAX el lote se salta: no gasta en bucle")

    def test_sin_mensajes_del_jefe_no_llama_a_la_ia(self):
        charla.agregar("isabelle", "Buenos días, jefe.", "1")
        llm = self.con(FakeLLM())
        self.assertFalse(asyncio.run(charla.actualizar()))
        self.assertEqual(llm.prompts, [])

    def test_espera_si_alguien_espera_respuesta(self):
        charla.agregar("jefe", "Piter es mi proveedor", "1")
        with db.connect() as c:  # el mensaje es viejo: no está escribiendo
            c.execute("UPDATE charla SET created_at = '2000-01-01T00:00:00+00:00'")
        llm = self.con(FakeLLM())
        self.assertEqual(asyncio.run(charla.revisar(lambda: True)), {})
        self.assertEqual(llm.prompts, [])

    def test_espera_la_pausa_de_openrouter(self):
        charla.agregar("jefe", "Piter es mi proveedor", "1")
        with db.connect() as c:
            c.execute("UPDATE charla SET created_at = '2000-01-01T00:00:00+00:00'")
        llm = self.con(FakeLLM())
        llm.pausa = 30
        self.assertEqual(asyncio.run(charla.revisar(lambda: False)), {})
        self.assertEqual(llm.prompts, [], "no llama a OpenRouter durante la pausa")

    def test_un_mensaje_nuevo_la_pospone(self):
        charla.agregar("jefe", "Piter es mi proveedor", "1")
        with db.connect() as c:
            c.execute("UPDATE charla SET created_at = '2000-01-01T00:00:00+00:00'")
        self.con(FakeLLM(espera=1))

        async def go():
            tarea = asyncio.create_task(charla.revisar(lambda: False))
            await asyncio.sleep(0.1)
            charla.pausar()  # llegó un mensaje del jefe
            return await tarea

        self.assertEqual(asyncio.run(go()), {"memoria_pospuesta": True})
        self.assertEqual(charla.ficha()["personas"], [])


if __name__ == "__main__":
    unittest.main()
