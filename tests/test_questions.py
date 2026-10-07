"""Reconocimiento de preguntas temporales y meta (sin base de datos).

    docker run --rm -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_questions
"""

import unittest
from datetime import date

from core.questions import RE_ABOUT, RE_LAST, RE_WHAT, _range

TODAY = date(2026, 10, 7)  # miércoles


class QuestionsTest(unittest.TestCase):
    def test_temporal(self):
        cases = {
            "¿Qué aprendí hoy?": ("2026-10-07", "2026-10-07"),
            "que te conté ayer": ("2026-10-06", "2026-10-06"),
            "¿Qué subí esta semana?": ("2026-10-05", "2026-10-07"),
            "¿Qué cosas guardé en los últimos 3 días?": ("2026-10-05", "2026-10-07"),
            "¿qué te envié la semana pasada?": ("2026-09-28", "2026-10-04"),
            "¿Qué aprendiste el lunes?": ("2026-10-05", "2026-10-05"),
        }
        for q, (a, b) in cases.items():
            with self.subTest(q=q):
                self.assertTrue(RE_WHAT.search(q))
                start, end, _ = _range(q, TODAY)
                self.assertEqual((start.isoformat(), end.isoformat()), (a, b))

    def test_no_temporal(self):
        for q in ("¿Cuántas lunas tiene Zorvak?", "¿Qué día paga Pedro?", "hoy llueve"):
            with self.subTest(q=q):
                self.assertFalse(RE_WHAT.search(q) and _range(q, TODAY))

    def test_last_and_about(self):
        self.assertTrue(RE_LAST.search("Resume lo último que subí"))
        self.assertTrue(RE_LAST.search("¿qué fue lo último que te envié?"))
        self.assertFalse(RE_LAST.search("¿Cuál fue el último pedido de Pedro?"))
        self.assertEqual(RE_ABOUT.match("¿Qué sabes de Pedro Gómez?").group(1), "Pedro Gómez")
        self.assertEqual(RE_ABOUT.match("háblame sobre la cooperativa").group(1), "la cooperativa")
        self.assertEqual(RE_ABOUT.match("¿Qué sabes del maíz morado?").group(1), "maíz morado")
        self.assertIsNone(RE_ABOUT.match("¿Qué día paga Pedro?"))


if __name__ == "__main__":
    unittest.main()
