"""Personalidad: límites de tratamientos y emojis por código, fuentes legibles, [AHORA] y lo que
Isabelle puede (y no puede) ofrecer. Sin red.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/p -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_persona
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from core import citations, clock, conversa, persona

TRATOS = ("jefe", "señor josias", "señor", "gerente", "ingeniero josias")


def tratos(texto: str) -> list[str]:
    return [m.group(1).lower() for m in persona._vocativos(texto)]


class DosificarTest(unittest.TestCase):
    def setUp(self):
        persona.get.cache_clear()
        persona.reiniciar()

    def test_tratamiento_en_la_mitad_como_mucho_y_uno_por_mensaje(self):
        salidas = [persona.dosificar(t) for t in (
            "Listo, jefe: anotado, jefe.", "Perfecto, señor.", "Claro, gerente. Aquí sigo, jefe.",
            "Jefe, ya es hora: llamar a Piter.", "Con gusto, señor Josias.", "Hola jefe.",
        )]
        usados = [tratos(s) for s in salidas]
        self.assertTrue(all(len(u) <= 1 for u in usados), salidas)
        self.assertLessEqual(sum(1 for u in usados if u), len(salidas) // 2, salidas)
        for a, b in zip(usados, usados[1:]):
            self.assertFalse(a and b, f"dos seguidos: {salidas}")
        self.assertEqual(salidas[0], "Listo, jefe: anotado.")
        self.assertEqual(salidas[1], "Perfecto.")
        self.assertEqual(salidas[3], "Jefe, ya es hora: llamar a Piter." if usados[3] else salidas[3])
        self.assertNotIn("jefe", salidas[5].lower() if not usados[5] else "")

    def test_no_repite_el_ultimo_tratamiento(self):
        persona.dosificar("Listo, jefe.")
        persona.dosificar("Perfecto.")
        s = persona.dosificar("Claro, jefe.")
        self.assertEqual(len(tratos(s)), 1)
        self.assertNotEqual(tratos(s), ["jefe"], s)

    def test_quitar_vocativo_deja_el_texto_bien(self):
        persona.dosificar("Hola, jefe.")  # el siguiente ya no lleva
        casos = {"Jefe, ya es hora: llamar.": "Ya es hora: llamar.",
                 "¡Bien hecho, señor!": "¡Bien hecho!",
                 "Dígame, gerente, ¿qué necesita?": "Dígame, ¿qué necesita?",
                 "Recibí tu foto, señor, y ya la guardé.": "Recibí tu foto y ya la guardé.",
                 "⏰ Jefe, le recuerdo: pagar.": "Le recuerdo: pagar."}
        for entrada, esperado in casos.items():
            persona.reiniciar()
            persona.dosificar("Hola, jefe.")
            self.assertEqual(persona.dosificar(entrada), esperado if "⏰" not in entrada else "⏰ " + esperado)

    def test_no_toca_tratamientos_que_no_son_vocativos(self):
        persona.dosificar("Hola, jefe.")
        self.assertEqual(persona.dosificar("Hable con su jefe de ventas."), "Hable con su jefe de ventas.")

    def test_emojis_uno_por_mensaje_y_uno_de_cada_tres(self):
        salidas = [persona.dosificar(t) for t in ("Qué bien 😊🎉", "Genial 👏", "Ánimo 💪", "Vamos 🚀", "Listo ✅")]
        con = [bool(persona.EMOJI.search(s)) for s in salidas]
        self.assertEqual(salidas[0], "Qué bien 😊")
        self.assertEqual(con, [True, False, False, True, False])
        self.assertEqual(salidas[1], "Genial")


class FuentesTest(unittest.TestCase):
    def test_referencia_legible(self):
        nota = citations.legible("nota.txt", "2026-11-02T20:00:00+00:00", "Rosa Quispe me debe 300 soles desde el lunes pasado")
        self.assertEqual(nota, "nota del 2 de nov sobre «Rosa Quispe me debe 300 soles…»")
        self.assertEqual(citations.legible("nota.txt", "2026-10-30T15:00:00+00:00", "Piter paga."), "nota del 30 de oct sobre «Piter paga»")
        self.assertEqual(citations.legible("nota_de_voz.ogg", "2026-11-01T15:00:00+00:00", "", "min 01:23"),
                         "nota de voz del 1 de nov, min 01:23")
        self.assertEqual(citations.legible("informe.pdf", "2026-11-01T15:00:00+00:00", "x", "pág. 3"), "informe.pdf del 1 de nov, pág. 3")
        self.assertNotIn("nota.txt", conversa._fuentes([{"filename": "nota.txt", "snippet": "Rosa paga los viernes"}]))

    def test_sin_segun_la_nota(self):
        self.assertEqual(conversa.sin_segun("Según la nota del lunes, Rosa paga los viernes."), "Rosa paga los viernes.")
        self.assertEqual(conversa.sin_segun("Según sus notas: Rosa debe 120."), "Rosa debe 120.")
        self.assertEqual(conversa.sin_segun("Rosa paga los viernes."), "Rosa paga los viernes.")

    def test_lineas_del_codigo_se_quitan_del_historial(self):
        persona.get.cache_clear()
        for v in persona.get()["frases"]["sin_memoria"]:
            linea = v.format(trato="jefe")
            self.assertEqual(conversa.sin_avisos(f"{linea}\n\nEs el retorno."), "Es el retorno.", v)
        self.assertEqual(conversa.sin_avisos("Rosa debe 120.\n\nDe mi memoria: nota del 2 de nov."), "Rosa debe 120.")
        self.assertEqual(len(persona.get()["frases"]["sin_memoria"]), 5)


class ContextoTest(unittest.TestCase):
    def test_ahora_con_dia_hora_12h_y_manana(self):
        with mock.patch.object(clock, "now_utc", lambda: datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)):
            a = conversa._ahora()
        self.assertIn("lunes 2 de noviembre de 2026, 7:30 p. m.", a)
        self.assertIn("de noche", a)
        self.assertIn("Mañana es martes 3 de noviembre", a)

    def test_capacidades_y_carino_medio_en_el_sistema(self):
        persona.get.cache_clear()
        s = persona.sistema()
        for texto in ("redactar borradores", "Nunca ofreces imprimir", "alarmas", "correos", "cariño medio",
                      "cansancio o desánimo", "Nunca eres sexual", "uno de cada tres", "12 horas"):
            self.assertIn(texto, s)
        self.assertNotIn("cariño alto", s)


if __name__ == "__main__":
    unittest.main()
