"""Pruebas del troceado por estructura (solo librería estándar).

Uso: python3 -m unittest tests.test_chunker
"""

import unittest

from core.chunker import Segment, chunk_segments, chunk_text

WORDS = "alfa beta gamma delta épsilon zeta eta theta iota kappa lambda mu".split()


def sentence(i: int) -> str:
    return f"La frase número {i} habla de {WORDS[i % len(WORDS)]} y de {WORDS[(i * 7) % len(WORDS)]}."


class ChunkerTest(unittest.TestCase):
    def test_no_corta_palabras(self):
        text = " ".join(sentence(i) for i in range(200))
        vocab = set(text.split())
        for chunk in chunk_text(text, size=300, overlap=80):
            self.assertLessEqual(len(chunk), 300 + 80 + 2)
            for w in chunk.split():
                self.assertIn(w, vocab, f"palabra partida: {w!r}")

    def test_solapamiento_en_frase(self):
        text = " ".join(sentence(i) for i in range(60))
        chunks = chunk_text(text, size=400, overlap=120)
        for c in chunks[1:]:
            self.assertTrue(c.startswith("La frase número"), c[:40])

    def test_secciones_markdown(self):
        md = "# Ventas\n\nPedro compró sillas.\n\n## Cobranzas\nLuis debe 200 soles.\n\nAna pagó."
        chunks = chunk_segments([Segment(md)], size=1000)
        self.assertEqual(len(chunks), 2, chunks)
        self.assertEqual(chunks[0][1]["seccion"], "Ventas")
        self.assertTrue(chunks[1][0].startswith("## Cobranzas"))
        self.assertEqual(chunks[1][1]["seccion"], "Cobranzas")
        self.assertNotIn("Pedro", chunks[1][0], "la sección nueva no arrastra la anterior")

    def test_paginas(self):
        pages = [Segment(" ".join(sentence(p * 10 + i) for i in range(8)), {"pagina": p}) for p in (1, 2, 3)]
        chunks = chunk_segments(pages, size=900, overlap=100)
        self.assertEqual(chunks[0][1]["pagina"], 1)
        self.assertTrue(any("pagina_fin" in m for _, m in chunks), "un trozo que cruza páginas lo indica")
        self.assertEqual(chunks[-1][1].get("pagina_fin", chunks[-1][1]["pagina"]), 3)

    def test_vacio(self):
        self.assertEqual(chunk_text("  \n\n "), [])


if __name__ == "__main__":
    unittest.main()


class CitationsTest(unittest.TestCase):
    def test_strip_markers(self):
        from core.citations import strip_markers

        self.assertEqual(strip_markers("Pedro paga los viernes [1, 2]."), "Pedro paga los viernes.")
        self.assertEqual(strip_markers("Fuente 1: Pedro paga."), "Pedro paga.")
        self.assertEqual(
            strip_markers("Según la Fuente 2, es activo (Fuente 1 y 3)."), "Según mis notas, es activo."
        )

    def test_supporting_and_location(self):
        from core.citations import cite, supporting

        hits = [
            {"event_id": "b", "filename": "y.txt", "content": "Pedro compra sillas", "meta": {}},
            {"event_id": "a", "filename": "x.pdf", "content": "El volcán Termola tiene cráteres gemelos",
             "meta": {"pagina": 2, "pagina_fin": 3}},
        ]
        best = supporting("Termola tiene cráteres gemelos", hits)
        self.assertEqual([cite(h) for h in best], ["x.pdf, pág. 2–3"])
        self.assertEqual(cite({"filename": "voz.ogg", "meta": {"inicio": 83.4}}), "voz.ogg, min 01:23")
