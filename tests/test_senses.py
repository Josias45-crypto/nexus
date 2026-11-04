"""Pruebas de los sentidos (extracción por formato). Corren dentro del contenedor:

    docker run --rm -e NEXUS_DATA_DIR=/tmp -v $PWD/tests:/app/tests:ro nexus-core \
        python -m unittest tests.test_senses
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.chunker import chunk_segments
from core.citations import location
from senses import Unsupported, extract, find
from tests import fixtures


class SensesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self, name: str, data: bytes | str) -> Path:
        path = self.dir / name
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def text_of(self, name: str, data, mime: str = "") -> tuple[str, list]:
        segs = extract(self.file(name, data), mime)
        return "\n".join(s.text for s in segs), segs

    def test_texto_codificaciones(self):
        text, _ = self.text_of("nota.txt", "Año de la cosecha: maíz".encode("cp1252"))
        self.assertIn("Año de la cosecha: maíz", text)
        text, _ = self.text_of("nota.txt", "﻿café".encode("utf-8"))
        self.assertEqual(text, "café")
        text, _ = self.text_of("sin_extension", "hola mundo\n")
        self.assertIn("hola mundo", text)

    def test_codigo_sin_encabezados(self):
        segs = extract(self.file("app.py", "# comentario\n\ndef f():\n    return 1\n"))
        chunks = chunk_segments(segs)
        self.assertNotIn("seccion", chunks[0][1])

    def test_csv_con_encabezado_y_filas(self):
        _, segs = self.text_of("v.csv", "cliente;monto\nRosa;120\nLuis;80\n")
        self.assertEqual(segs[0].text, "cliente: Rosa; monto: 120")
        self.assertEqual(segs[1].meta, {"fila": 3})

    def test_json_y_xml(self):
        text, _ = self.text_of("d.json", '{"cliente": {"nombre": "Rosa", "deuda": [10, 20]}}')
        self.assertIn("cliente.nombre: Rosa", text)
        self.assertIn("cliente.deuda[1]: 20", text)
        text, _ = self.text_of("d.xml", "<pedido><cliente>Rosa</cliente><total>99</total></pedido>")
        self.assertIn("cliente: Rosa", text)

    def test_html(self):
        html = "<html><head><style>x</style></head><body><h2>Precios</h2><p>Té&nbsp;verde 5</p><script>no()</script></body></html>"
        text, _ = self.text_of("p.html", html)
        self.assertIn("## Precios", text)
        self.assertIn("Té verde 5", text)
        self.assertNotIn("no()", text)

    def test_docx(self):
        data = fixtures.docx("Garantías", ["La garantía dura doce meses."], [["Producto", "Precio"], ["Silla", "40"]])
        text, segs = self.text_of("c.docx", data)
        self.assertIn("# Garantías", text)
        self.assertIn("Silla | 40", text)
        self.assertEqual(chunk_segments(segs)[0][1].get("seccion"), "Garantías")

    def test_xlsx(self):
        data = fixtures.xlsx({"Ventas": [["Cliente", "Monto"], ["Rosa", 120], ["Luis", 80.5]], "Vacía": []})
        _, segs = self.text_of("v.xlsx", data)
        rows = [s for s in segs if "fila" in s.meta]
        self.assertEqual(rows[0].text, "Cliente: Rosa; Monto: 120")
        self.assertEqual(rows[1].meta, {"hoja": "Ventas", "fila": 3})
        meta = chunk_segments(segs)[0][1]
        self.assertEqual(location(meta), "hoja Ventas, fila 2–3")

    def test_pptx_orden_y_notas(self):
        data = fixtures.pptx([("Inicio", "Bienvenida"), ("Plan", "Abrir local nuevo")], notes={2: "Decidir en marzo"})
        _, segs = self.text_of("p.pptx", data)
        bodies = [s for s in segs if not s.text.startswith("#")]
        self.assertIn("Bienvenida", bodies[0].text)
        self.assertEqual(bodies[1].meta, {"diapositiva": 2})
        self.assertIn("Notas: Decidir en marzo", bodies[1].text)
        self.assertNotIn("Notas: Decidir en marzo 2", bodies[1].text)

    def test_epub(self):
        data = fixtures.epub([("Uno", "Primer capítulo"), ("Dos", "Segundo capítulo")])
        _, segs = self.text_of("l.epub", data)
        self.assertEqual([s.meta for s in segs], [{"capitulo": 1}, {"capitulo": 2}])
        self.assertIn("# Dos", segs[1].text)

    def test_eml_con_adjunto(self):
        data = fixtures.eml("Cotización", "Hola, adjunto la cotización.", {"precios.csv": b"item,precio\nmesa,90\n", "foto.heic": b"x"})
        text, segs = self.text_of("c.eml", data)
        self.assertIn("Asunto: Cotización", text)
        self.assertIn("item: mesa; precio: 90", text)
        self.assertEqual(location([s for s in segs if "fila" in s.meta][0].meta), "precios.csv, fila 2")

    def test_zip(self):
        inner = fixtures.zip_of({"z/otro.txt": "anidado"})
        data = fixtures.zip_of({
            "a/nota.md": "# Tema\n\nContenido del zip",
            "b/datos.json": '{"k": "v"}',
            "musica.mp3": b"ID3",
            "__MACOSX/._nota.md": b"x",
            "interno.zip": inner,
        })
        text, segs = self.text_of("todo.zip", data)
        self.assertIn("Contenido del zip", text)
        self.assertIn("k: v", text)
        self.assertIn("anidado", text)
        self.assertNotIn("ID3", text)
        metas = [m for _, m in chunk_segments(segs)]
        self.assertTrue(any(location(m) == "a/nota.md, § Tema" for m in metas), metas)

    def test_no_soportados_con_motivo(self):
        with self.assertRaisesRegex(Unsupported, r"\.docx"):
            extract(self.file("viejo.doc", b"\xd0\xcf\x11\xe0" + b"\x00" * 100))
        with self.assertRaisesRegex(Unsupported, "no soportado"):
            extract(self.file("cosa.bin", b"\x00\x01\x02"))
        with self.assertRaisesRegex(Unsupported, "contraseña o dañado"):
            extract(self.file("roto.docx", b"no es zip"))
        self.assertIsNone(find(Path("x.rar"), "application/zip"))

    def test_pdf_escaneado_va_a_vision(self):
        """Sin OCR local: las páginas sin texto no salen de aquí; core/medios.py las manda al modelo
        de visión (Fase 3). escaneadas() las detecta y paginas_jpeg() las prepara."""
        from senses import documentos

        path = self.file("escaneo.pdf", fixtures.scanned_pdf(["Factura 4471", "Cliente Rosa Paredes"]))
        self.assertEqual(extract(path, "application/pdf"), [])
        textos = documentos.textos(path)
        self.assertEqual(documentos.escaneadas(textos), [1])
        jpeg = documentos.paginas_jpeg(path, [1])[1]
        self.assertTrue(jpeg.startswith(b"\xff\xd8"), "la página va como JPEG")

    def test_fotos_y_audios_no_son_de_senses(self):
        """Fotos, audios y videos los analiza core/medios.py; dentro de un .zip se piden aparte."""
        for name in ("foto.png", "nota.ogg", "video.mp4"):
            with self.subTest(name=name), self.assertRaises(Unsupported):
                extract(self.file(name, b"\x00\x01binario\x02"))
        segs = extract(self.file("c.zip", fixtures.zip_of({"foto.png": fixtures.image_with_text(["x"]), "n.txt": "Nota Velbra"})))
        self.assertIn("Velbra", " ".join(s.text for s in segs))

if __name__ == "__main__":
    os.environ.setdefault("NEXUS_DATA_DIR", tempfile.gettempdir())
    unittest.main()
