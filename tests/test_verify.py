"""Pruebas de la verificación anti-invención (core/verify.py)."""

import unittest

from core.verify import names, unverified

FUENTES = [
    "Ferretería Los Andes debe 1850 soles desde agosto.",
    "El Colegio Santa Úrsula pagará los 25 proyectores en tres cuotas.",
    "Pedido de Bodega El Trigal: 40 sacos de harina. El contacto es Hernán.",
]


class VerifyTest(unittest.TestCase):
    def test_gil_maruri_inventado(self):
        answer = "Hernán, Ferretería Los Andes y Gil Maruri pagan en partes."
        self.assertEqual(unverified(answer, FUENTES), ["Gil Maruri"])

    def test_cifras_con_formato_y_en_palabras(self):
        answer = "La Ferretería Los Andes debe 1.850 soles y el colegio paga en 3 cuotas."
        self.assertEqual(unverified(answer, FUENTES), [])
        self.assertEqual(unverified("Debe 2000 soles.", FUENTES), ["2000"])

    def test_inicio_de_frase_y_pregunta(self):
        self.assertEqual(unverified("Según las fuentes, la bodega pidió 40 sacos.", FUENTES), [])
        self.assertEqual(unverified("Pedro no aparece.", FUENTES, "¿Qué quería Pedro?"), [])

    def test_nombres_separados(self):
        self.assertEqual(
            names("Gilberto Mamani y Rosaura Quispe pidieron laptops; también el Restaurante La Brasa."),
            ["Gilberto Mamani", "Rosaura Quispe", "Restaurante La Brasa"],
        )


if __name__ == "__main__":
    unittest.main()


class AskRegenerationTest(unittest.TestCase):
    """/ask regenera una vez si inventa un nombre y, si insiste, avisa con ⚠."""

    def _ask(self, replies: list[str]) -> tuple[dict, list]:
        import asyncio
        from unittest import mock

        from core import ask

        hit = {"event_id": "e1", "filename": "nota.txt", "position": 0, "content": FUENTES[0],
               "meta": {}, "distance": 0.5, "private": 0, "termino_raro": False, "coincide_texto": True}
        calls = []

        class FakeLLM:
            async def chat_ex(self, messages, **kw):
                calls.append(messages)
                return replies[len(calls) - 1], {"proveedor": "falso", "modelo": "x", "nube": False}

        async def no_route(q):
            return None

        async def fake_search(*a, **kw):
            return [hit]

        with mock.patch("core.questions.route", no_route), \
                mock.patch.object(ask, "search", fake_search), \
                mock.patch.object(ask, "get_provider", lambda: FakeLLM()), \
                mock.patch.object(ask, "publish_recall", lambda *a: None):
            return asyncio.run(ask.ask("¿Quién debe dinero?")), calls

    def test_regenera_y_corrige(self):
        r, calls = self._ask(["Gil Maruri debe 1850 soles.", "Ferretería Los Andes debe 1850 soles."])
        self.assertEqual(len(calls), 2)
        self.assertIn("Gil Maruri", calls[1][-1]["content"])
        self.assertEqual(r["answer"], "Ferretería Los Andes debe 1850 soles.")
        self.assertEqual(r["sin_verificar"], [])

    def test_avisa_si_persiste(self):
        r, _ = self._ask(["Gil Maruri debe 1850 soles.", "Gil Maruri debe 1850 soles."])
        self.assertTrue(r["answer"].endswith("⚠ No pude verificar: Gil Maruri"))
