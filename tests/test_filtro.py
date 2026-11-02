"""Filtro de salida (solo para el texto que genera la IA).

    python3 -m unittest tests.test_filtro   (no necesita la imagen)
"""

import unittest

from core.filtro import limpiar


class FiltroTest(unittest.TestCase):
    def test_markdown(self):
        self.assertEqual(limpiar("**Hola**, jefe. ## Título\n`código`"), "Hola, jefe. ## Título\ncódigo")
        self.assertEqual(limpiar("# Resumen\nTodo bien"), "Resumen\nTodo bien")

    def test_gestos(self):
        self.assertEqual(limpiar("*sonríe* Claro que sí, jefe."), "Claro que sí, jefe.")
        self.assertEqual(limpiar("Listo (guiña el ojo), jefe."), "Listo, jefe.")
        self.assertEqual(limpiar("Eso es *muy* importante."), "Eso es muy importante.")

    def test_respeta_lo_que_no_es_gesto(self):
        for t in ("Llame a Piter (04:17, hora de Perú).", "Rosa debe 120 soles [1].",
                  "Rosa debe 120 soles.\n\nFuentes: ventas.xlsx, fila 3", "Opciones:\n- local\n- en línea"):
            with self.subTest(t=t):
                self.assertEqual(limpiar(t), t)

    def test_comillas_que_envuelven(self):
        self.assertEqual(limpiar("«Buenos días, jefe.»"), "Buenos días, jefe.")
        self.assertEqual(limpiar('Dijo "hola" y se fue.'), 'Dijo "hola" y se fue.')

    def test_vacio(self):
        self.assertEqual(limpiar("*sonríe*"), "")
        self.assertEqual(limpiar(""), "")


if __name__ == "__main__":
    unittest.main()
