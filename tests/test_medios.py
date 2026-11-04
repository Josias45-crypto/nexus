"""Fase 3 sin red: ver y oír con proveedores simulados (visión, transcripción, comentario y embeddings).

    docker run --rm -e NEXUS_DATA_DIR=/tmp/m -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_medios
"""

import asyncio
import io
import json
import math
import struct
import unittest
import wave
from datetime import datetime, timezone
from unittest import mock

from config import settings
from core import ask, charla, clock, conversa, db, inbox, medios, memory, pendientes, persona, processor, reminders
from tests import fixtures

CANAL, CHAT = "simulado", "1001"
AHORA = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)  # martes 10:00 en Perú
BOLETA = {"tipo": "boleta", "descripcion": "Una boleta de venta impresa sobre una mesa.",
          "texto": "BOLETA B001-4517\nImpresora Epson L3250 S/ 450.00\nVence: 28/11/2026",
          "datos": [{"tipo": "monto", "valor": "S/ 450.00", "que": "total"},
                    {"tipo": "fecha", "valor": "28/11/2026", "que": "vencimiento"},
                    {"tipo": "monto", "valor": "S/ 999.00", "que": "inventado"}],
          "ilegible": ""}
PERRO = {"tipo": "foto", "descripcion": "Un perro marrón echado en un sofá azul.", "texto": "", "datos": [], "ilegible": ""}


def wav(segundos: float, rate: int = 16000) -> bytes:
    """Tono de 440 Hz (el contenido da igual: la transcripción es simulada)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(int(segundos * rate))))
    return buf.getvalue()


def pdf_escaneado(paginas: int) -> bytes:
    from PIL import Image

    imgs = [Image.new("RGB", (300, 400), "white") for _ in range(paginas)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:], resolution=72)
    return buf.getvalue()


class IAFalsa:
    """Visión, transcripción y chat simulados; cuenta las llamadas de cada tipo."""

    vision_model, audio_model, model = "vision/falso", "audio/falso", "chat/falso"

    def __init__(self):
        self.vision = BOLETA
        self.transcripcion = {"text": "Hola", "segments": [{"start": 0, "end": 2, "text": "Hola, ¿cómo estás?", "avg_logprob": -0.2, "no_speech_prob": 0.01}]}
        self.comentario = "¡Qué buena foto! ¿De dónde es?"
        self.llamadas = {"ver": 0, "transcribir": 0, "chat": 0}
        self.segundos: list[float] = []

    async def ver(self, prompt, imagenes, max_tokens=900):
        self.llamadas["ver"] += 1
        return json.dumps(self.vision, ensure_ascii=False), {"modelo": self.vision_model, "costo_usd": 0.0001}

    async def transcribir(self, audio, formato, idioma="es", vocabulario=()):
        import av

        with av.open(io.BytesIO(audio)) as c:
            self.segundos.append(sum(f.samples for f in c.decode(audio=0)) / 48000)
        self.llamadas["transcribir"] += 1
        return self.transcripcion, {"modelo": self.audio_model, "costo_usd": 0.00002}

    async def chat_ex(self, messages, **kw):
        self.llamadas["chat"] += 1
        if "Coméntalo" in messages[-1]["content"]:
            return self.comentario, {"costo_usd": 0.00005}
        return "Anotado, jefe.", {}

    async def chat(self, messages, **kw):
        return (await self.chat_ex(messages, **kw))[0]


class EmbedFalso:
    async def embed_documents(self, textos):
        return [[0.01] * settings.EMBED_DIM for _ in textos]

    async def embed_query(self, texto):
        return [0.01] * settings.EMBED_DIM


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("reminders", "outbox", "pending_confirmations", "charla", "kv", "digests", "medios"):
                c.execute(f"DELETE FROM {t}")
            for r in c.execute("SELECT id FROM events").fetchall():
                memory.delete_chunks(c, r["id"])
            c.execute("DELETE FROM events")
        persona.get.cache_clear()
        persona.reiniciar()
        self.ia = IAFalsa()
        parches = [mock.patch.object(clock, "now_utc", lambda: AHORA)]
        parches += [mock.patch(d, lambda: self.ia) for d in ("core.medios.get_client", "core.conversa.get_client")]
        parches += [mock.patch(d, lambda: EmbedFalso()) for d in ("core.medios.get_embedder", "core.processor.get_embedder")]
        parches += [mock.patch.object(ask, "ask", self._sin_memoria), mock.patch.object(conversa, "search", self._nada),
                    mock.patch.object(conversa, "interpretar", self._interpretar)]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    async def _sin_memoria(self, pregunta, **kw):
        return {"answer": ask.NO_INFO, "sources": []}

    async def _nada(self, *a, **kw):
        return []

    async def _interpretar(self, texto, previos):
        return {"accion": "charlar", "consulta": ""}

    def enviar(self, *archivos, leyenda="", voz=False, grande=False) -> dict:
        items = []
        for nombre, data, mime in archivos:
            r = inbox.save(data, nombre, mime, CANAL)
            items.append({"id": r["id"], "filename": nombre, "kind": r["kind"], "duplicate": r["duplicate"]})
        self.items = items
        return asyncio.run(conversa.archivos(items, CANAL, CHAT, leyenda, voz, grande))

    def decir(self, texto: str) -> dict:
        return asyncio.run(conversa.conversar([texto], CANAL, CHAT))

    def trozos(self, event_id: str) -> list[str]:
        with db.connect() as c:
            return [r["content"] for r in c.execute("SELECT content FROM chunks WHERE event_id = ?", (event_id,))]

    def estado(self, event_id: str) -> dict:
        with db.connect() as c:
            return dict(c.execute("SELECT status, error FROM events WHERE id = ?", (event_id,)).fetchone())


class HuellaTest(Base):
    def test_se_analiza_una_vez_y_el_repetido_reutiliza(self):
        foto = fixtures.image_with_text(["Boleta"])
        r = self.enviar(("boleta.jpg", foto, "image/jpeg"))
        eid = self.items[0]["id"]
        self.assertEqual(self.ia.llamadas["ver"], 1)
        self.assertEqual(self.estado(eid)["status"], "processed")
        self.assertTrue(any("boleta de venta" in t for t in self.trozos(eid)), "la descripción quedó en la memoria")
        # El mismo archivo otra vez (aunque se llame distinto): misma huella, sin IA
        r = self.enviar(("otra.jpg", foto, "image/jpeg"))
        self.assertTrue(self.items[0]["duplicate"])
        self.assertEqual(self.ia.llamadas["ver"], 1)
        self.assertRegex(r["texto"], r"ya me la había mostrado|ya la tengo guardada")
        self.assertIn("una boleta de venta", r["texto"])
        # Reindexar rearma los trozos desde lo guardado: tampoco llama a la IA
        with db.connect() as c:
            c.execute("UPDATE events SET status = 'pending' WHERE id = ?", (eid,))
        asyncio.run(processor.process_pending(5))
        self.assertEqual(self.ia.llamadas["ver"], 1)
        self.assertEqual(self.estado(eid)["status"], "processed")

    def test_solo_vuelve_a_mirar_si_el_jefe_lo_pide(self):
        self.ia.vision = PERRO
        self.enviar(("perro.jpg", fixtures.image_with_text(["guau"]), "image/jpeg"))
        self.ia.comentario = "¡Qué lindo!"
        self.decir("¿qué había en la foto?")
        self.assertEqual(self.ia.llamadas["ver"], 1, "una pregunta usa lo guardado")
        self.ia.vision = {**PERRO, "descripcion": "Un perro marrón con collar rojo en un sofá azul."}
        r = self.decir("vuelve a mirar la foto")
        self.assertEqual(self.ia.llamadas["ver"], 2)
        self.assertEqual(r["accion"], "archivo")
        self.assertIn("collar rojo", medios.get(self.items[0]["id"])["descripcion"])

    def test_los_datos_solo_valen_si_estan_en_el_texto(self):
        a = medios.validar(BOLETA)
        self.assertEqual([d["valor"] for d in a["datos"]], ["S/ 450.00", "28/11/2026"], "el S/ 999.00 no está en el texto")
        with self.assertRaises(ValueError):
            medios.validar({"descripcion": "", "texto": ""})


class FotosTest(Base):
    def test_cifras_se_piden_confirmar_y_el_si_queda(self):
        self.ia.comentario = "Ya vi tu boleta, está clarita."
        r = self.enviar(("boleta.jpg", fixtures.image_with_text(["b"]), "image/jpeg"))
        self.assertIn("Leí S/ 450.00 (total), 28/11/2026 (vencimiento); ¿es correcto?", r["texto"])
        self.assertIn("dígame cuándo", r["texto"])
        self.assertNotIn("999", r["texto"])
        r = self.decir("sí")
        self.assertIn("confirmado", r["texto"])
        self.assertEqual(medios.get(self.items[0]["id"])["confirmado"], 1)

    def test_se_llama_max_queda_ligado_a_la_foto(self):
        self.ia.vision = PERRO
        self.ia.comentario = "¡Qué ternura, jefe! ¿Cómo se llama?"
        r = self.enviar(("perro.jpg", fixtures.image_with_text(["guau"]), "image/jpeg"))
        self.assertIn("¿Cómo se llama?", r["texto"])
        self.assertTrue(pendientes.hay(CANAL, CHAT, "medio"))
        self.decir("se llama Max")
        eid = self.items[0]["id"]
        self.assertEqual([n["texto"] for n in medios.get(eid)["notas"]], ["se llama Max"])
        self.assertTrue(any("se llama Max" in t for t in self.trozos(eid)), "la respuesta es un trozo del original")
        # Si no responde la pregunta, se descarta y nada se liga
        self.enviar(("perro2.jpg", fixtures.image_with_text(["otro"]), "image/jpeg"))
        self.decir("gracias")
        self.assertEqual(medios.get(self.items[0]["id"])["notas"], [])

    def test_ilegible_se_dice(self):
        self.ia.vision = {**BOLETA, "datos": [], "ilegible": "la fecha de abajo"}
        self.ia.comentario = "Ya la vi."
        r = self.enviar(("b.jpg", fixtures.image_with_text(["x"]), "image/jpeg"))
        self.assertIn("no se leía bien: la fecha de abajo", r["texto"])

    def test_album_una_sola_respuesta(self):
        self.ia.vision = PERRO
        self.ia.comentario = "¡Qué lindas fotos! ¿Son de tu casa?"
        r = self.enviar(("a.jpg", fixtures.image_with_text(["1"]), "image/jpeg"),
                        ("b.jpg", fixtures.image_with_text(["2"]), "image/jpeg"),
                        ("c.jpg", fixtures.image_with_text(["3"]), "image/jpeg"))
        self.assertEqual(self.ia.llamadas["ver"], 3)
        comentarios = self.ia.llamadas["chat"]
        self.assertEqual(comentarios, 1, "un solo comentario para el álbum")
        self.assertEqual(r["texto"].count("?"), 1)

    def test_pdf_de_mas_de_20_paginas_pregunta_antes_de_seguir(self):
        self.ia.vision = {**PERRO, "tipo": "documento", "descripcion": "Una página escaneada.", "texto": "Contrato de alquiler"}
        self.ia.comentario = "Ya leí el documento."
        r = self.enviar(("contrato.pdf", pdf_escaneado(22), "application/pdf"))
        self.assertEqual(self.ia.llamadas["ver"], 20)
        self.assertIn("Tiene 22 páginas escaneadas", r["texto"])
        self.assertIn("leí las primeras 20", r["texto"])
        self.assertIn("¿Sigo?", r["texto"])
        self.assertEqual(r["texto"].count("?"), 1)
        r = self.decir("sí")
        self.assertEqual(self.ia.llamadas["ver"], 22)
        self.assertIn("leí las otras 2 páginas", r["texto"])
        self.assertEqual(len(medios.get(self.items[0]["id"])["partes"]), 22)


class ComentarioTest(Base):
    def test_verificacion(self):
        ctx = "Foto (foto): Un perro marrón echado en un sofá azul."
        self.assertIsNone(medios.verificar("¡Qué ternura de perrito en su sofá azul! ¿Cómo se llama?", ctx, 1))
        self.assertIn("cifra", medios.verificar("Ese perro debe pesar 30 kilos.", ctx, 1))
        self.assertIn("nombre", medios.verificar("¡Qué lindo, se parece a Firulais de la tele!", ctx, 1))
        self.assertIn("supone", medios.verificar("Se ve que lo quieres mucho.", ctx, 1))
        self.assertEqual(medios.verificar("¿Es tuyo? ¿Cómo se llama?", ctx, 1), "preguntas")
        self.assertEqual(medios.verificar("Lindo. ¿Es tuyo?", ctx, 0), "preguntas")
        ctx2 = "Documento: Factura de Josué Peñaloza por 581.00"
        self.assertIn("lo llama", medios.verificar("¡Qué buena compra, Josué!", ctx2, 0, ["Josué Peñaloza"]))

    def test_si_inventa_queda_el_respaldo(self):
        self.ia.vision = PERRO
        self.ia.comentario = "Se ve que es tu perro favorito, seguro se llama Toby."
        r = self.enviar(("p.jpg", fixtures.image_with_text(["p"]), "image/jpeg"))
        self.assertEqual(self.ia.llamadas["chat"], medios.COMENTARIO_INTENTOS)
        self.assertIn("un perro marrón echado en un sofá azul", r["texto"])
        self.assertNotIn("Toby", r["texto"])


class AudioTest(Base):
    def test_alucinaciones(self):
        segs = [
            {"start": 0, "end": 2, "text": "Subtítulos realizados por la comunidad de Amara.org", "avg_logprob": -0.3, "no_speech_prob": 0.1},
            {"start": 2, "end": 3, "text": " ¡Gracias!", "avg_logprob": -0.4, "no_speech_prob": 0.2},
            {"start": 3, "end": 4, "text": "mmm algo", "avg_logprob": -1.4, "no_speech_prob": 0.8},
            {"start": 4, "end": 5, "text": "gracias gracias gracias gracias gracias", "avg_logprob": -0.2, "no_speech_prob": 0.1, "compression_ratio": 3.1},
            {"start": 5, "end": 8, "text": " Mañana pago la luz.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
        ]
        buenos, dudosa = medios.limpiar_transcripcion(segs)
        self.assertEqual([s["text"].strip() for s in buenos], ["Mañana pago la luz."])
        self.assertFalse(dudosa)
        self.assertTrue(medios.limpiar_transcripcion(segs[:4])[1], "solo alucinaciones: dudosa")

    def test_vocabulario_del_negocio(self):
        vocab = medios.config()["vocabulario"]
        self.assertIn("IGV", vocab)
        self.assertEqual(medios.corregir_vocabulario("Recuérdame pagar el IGB a la SUNAT", vocab),
                         "Recuérdame pagar el IGV a la SUNAT")
        self.assertEqual(medios.corregir_vocabulario("la voleta y la Factura", vocab), "la boleta y la Factura")
        self.assertEqual(medios.corregir_vocabulario("vamos a la playa", vocab), "vamos a la playa")
        texto = "Pagar el IGB a la SUNAT."
        self.ia.transcripcion = {"text": texto, "segments": [{"start": 0, "end": 3, "text": texto, "avg_logprob": -0.2}]}
        self.ia.vocab = None
        original = self.ia.transcribir

        async def con_vocab(audio, formato, idioma="es", vocabulario=()):
            self.ia.vocab = list(vocabulario)
            return await original(audio, formato, idioma)

        self.ia.transcribir = con_vocab
        self.enviar(("reunion.wav", wav(2), "audio/wav"))
        self.assertEqual(self.ia.vocab, vocab, "el vocabulario va como indicación")
        self.assertIn("IGV", medios.get(self.items[0]["id"])["texto"])

    def test_transcripcion_vacia_no_se_inventa(self):
        self.ia.transcripcion = {"text": "", "segments": []}
        r = self.enviar(("nota_de_voz.ogg", wav(2), "audio/wav"), voz=True)
        self.assertRegex(r["texto"], r"No lo escuché bien|no se entendía bien")
        self.assertEqual(self.estado(self.items[0]["id"])["status"], "empty")
        self.assertEqual(self.trozos(self.items[0]["id"]), [])

    def test_tope_de_duracion(self):
        self.ia.transcripcion = {"text": "Reunión con el banco sobre el préstamo del local.",
                                 "segments": [{"start": 0, "end": 3, "text": "Reunión con el banco sobre el préstamo del local.", "avg_logprob": -0.2}]}
        cfg = {**medios.config(), "audio_max_min": 0.05}  # 3 segundos
        with mock.patch.object(medios, "config", lambda: cfg), mock.patch.object(conversa.medios, "config", lambda: cfg):
            r = self.enviar(("reunion.wav", wav(8), "audio/wav"))
        m = medios.get(self.items[0]["id"])
        self.assertAlmostEqual(m["duracion_s"], 8, delta=0.5)
        self.assertAlmostEqual(m["transcrito_s"], 3, delta=0.5)
        self.assertAlmostEqual(sum(self.ia.segundos), 3, delta=0.5, msg="solo viaja el tramo permitido")
        self.assertIn("Era un audio de", r["texto"])

    def test_nota_de_voz_corta_es_como_si_la_escribiera(self):
        texto = "Isabel, recuérdame mañana a las 10 am llamar al contador."
        self.ia.transcripcion = {"text": texto, "segments": [{"start": 0, "end": 4, "text": texto, "avg_logprob": -0.2}]}
        r = self.enviar(("nota_de_voz.ogg", wav(2), "audio/wav"), voz=True)
        self.assertIn("llamar al contador", r["texto"])
        self.assertEqual([x["text"] for x in reminders.list_open(CHAT)], ["llamar al contador"])
        self.assertTrue(any("[Nota de voz]" in m["texto"] for m in charla.recientes(10, CHAT)))
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE kind = 'text'").fetchone()[0], 0,
                             "no se duplica como nota: ya está guardada con su audio")


class VideoYGrandeTest(Base):
    def test_video_solo_se_guarda(self):
        r = self.enviar(("video.mp4", b"\x00\x00\x00\x18ftypmp42video-falso", "video/mp4"))
        eid = self.items[0]["id"]
        self.assertRegex(r["texto"], r"solo los guardo|no los veo")
        self.assertEqual(sum(self.ia.llamadas.values()), 0, "no se analiza")
        self.assertEqual(medios.get(eid)["estado"], "guardado")
        self.assertIn("solo se guardan", self.estado(eid)["error"])
        r = self.decir("¿qué dice el video?")
        self.assertRegex(r["texto"], r"solo lo guardé|solo los guardo")

    def test_mas_de_20_mb(self):
        r = asyncio.run(conversa.archivos([], CANAL, CHAT, grande=True))
        self.assertIn("20 MB", r["texto"])
        self.assertIn("web", r["texto"])


if __name__ == "__main__":
    unittest.main()
