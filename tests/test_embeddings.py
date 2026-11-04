"""Fase 4 sin red: embeddings por OpenRouter (lotes, reintentos, prefijos, caché), búsqueda que cae
a solo palabras, reindexado de solo vectores y espera sin gastar intentos.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/e -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_embeddings
"""

import asyncio
import json
import unittest
from unittest import mock

import httpx
import sqlite_vec

from config import settings
from core import ask, db, inbox, memory, processor, reindex, search
from providers import embeddings, openrouter
from providers.openrouter import ChatUnavailable, OpenRouter

DIM = settings.EMBED_DIM


def vector(texto: str) -> list[float]:
    """Vector fijo por texto (sin normalizar: el cliente normaliza)."""
    base = [float((sum(map(ord, texto)) * (i + 3)) % 17 + 1) for i in range(DIM)]
    return base


class Red:
    """OpenRouter /embeddings simulado: responde por lotes y cuenta peticiones y textos."""

    def __init__(self, fallas: int = 0, dim: int = DIM):
        self.cuerpos: list[dict] = []
        self.fallas = fallas
        self.dim = dim

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.cuerpos.append(body)
        if self.fallas:
            self.fallas -= 1
            return httpx.Response(503)
        data = [{"index": i, "embedding": vector(t)[: self.dim] + [1.0] * (self.dim - DIM)} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 10, "cost": 0.000001}})


def cliente(red: Red, modelo: str = "baai/bge-m3") -> OpenRouter:
    return OpenRouter("k", "chat", None, None, httpx.MockTransport(red), embed_model=modelo, embed_dim=DIM)


class ClienteTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(openrouter, "BACKOFF", 0)
        p.start()
        self.addCleanup(p.stop)

    def test_lotes_politica_y_vectores_normalizados(self):
        red = Red()
        textos = [f"nota {i}" for i in range(openrouter.EMBED_LOTE + 5)]
        vecs = asyncio.run(cliente(red).embed(textos))
        self.assertEqual(len(red.cuerpos), 2, "dos lotes")
        self.assertEqual([len(c["input"]) for c in red.cuerpos], [openrouter.EMBED_LOTE, 5])
        self.assertEqual(red.cuerpos[0]["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual(red.cuerpos[0]["model"], "baai/bge-m3")
        self.assertEqual(red.cuerpos[0]["input"][0], "nota 0", "bge-m3 no lleva prefijo")
        self.assertEqual(len(vecs), len(textos))
        self.assertAlmostEqual(sum(x * x for x in vecs[0]), 1.0, places=5)

    def test_prefijos_solo_si_el_modelo_los_pide(self):
        red = Red()
        asyncio.run(cliente(red, "intfloat/multilingual-e5-large").embed(["hola"], consulta=True))
        asyncio.run(cliente(red, "intfloat/multilingual-e5-large").embed(["hola"]))
        self.assertEqual([c["input"][0] for c in red.cuerpos], ["query: hola", "passage: hola"])

    def test_reintenta_y_luego_avisa(self):
        red = Red(fallas=2)
        self.assertEqual(len(asyncio.run(cliente(red).embed(["x"]))), 1)
        self.assertEqual(len(red.cuerpos), 3, "dos fallas 503 y el reintento bueno")
        with self.assertRaises(ChatUnavailable) as e:
            asyncio.run(cliente(Red(fallas=10)).embed(["x"]))
        self.assertEqual(e.exception.reason, "caido")

    def test_dimension_distinta_no_se_guarda(self):
        with self.assertRaises(ChatUnavailable) as e:
            asyncio.run(cliente(Red(dim=DIM + 4)).embed(["x"]))
        self.assertIn("NEXUS_EMBED_DIM", str(e.exception))


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for r in c.execute("SELECT id FROM events").fetchall():
                memory.delete_chunks(c, r["id"])
            for t in ("digests", "medios", "events", "embed_cache"):
                c.execute(f"DELETE FROM {t}")
        self.red = Red()
        self.cliente = cliente(self.red, settings.EMBED_MODEL)
        for p in (mock.patch.object(embeddings, "get_client", lambda: self.cliente),
                  mock.patch.object(processor, "get_client", lambda: self.cliente),
                  mock.patch.object(openrouter, "BACKOFF", 0)):
            p.start()
            self.addCleanup(p.stop)


class CacheTest(Base):
    def test_un_texto_repetido_no_se_vuelve_a_pedir(self):
        e = embeddings.get_embedder()
        a = asyncio.run(e.embed_documents(["Rosa debe 120 soles", "Tralvex entrega los martes"]))
        b = asyncio.run(e.embed_documents(["Tralvex entrega los martes", "Rosa debe 120 soles", "nueva"]))
        self.assertEqual(sum(len(c["input"]) for c in self.red.cuerpos), 3, "solo «nueva» se pidió otra vez")
        self.assertEqual([round(x, 5) for x in a[0]], [round(x, 5) for x in b[1]])
        asyncio.run(e.embed_query("Rosa debe 120 soles"))
        self.assertEqual(len(self.red.cuerpos), 3, "la pregunta tiene su propia huella (consulta)")


class ProcesoYBusquedaTest(Base):
    def guardar(self, texto: str) -> str:
        eid = inbox.save(texto.encode(), "nota.txt", "text/plain", "prueba", kind="text")["id"]
        asyncio.run(processor.process_pending(5))
        return eid

    def test_si_openrouter_falla_el_procesamiento_espera_sin_gastar_intentos(self):
        self.red.fallas = 99
        eid = inbox.save(b"La caja chica tiene 340 soles", "nota.txt", "text/plain", "prueba", kind="text")["id"]
        asyncio.run(processor.process_pending(5))
        with db.connect() as c:
            fila = c.execute("SELECT status, attempts, raw_path FROM events WHERE id = ?", (eid,)).fetchone()
        self.assertEqual((fila["status"], fila["attempts"]), ("pending", 0))
        self.assertTrue(open(fila["raw_path"], "rb").read().startswith(b"La caja chica"), "el original queda")
        self.assertGreater(self.cliente.paused_for(), 0, "la cola espera con pausa")
        self.assertEqual(asyncio.run(processor.process_pending(5)).get("en_pausa_s", 0) > 0, True)

    def test_sin_embeddings_busca_solo_por_palabras_y_lo_dice(self):
        self.guardar("El proveedor Brontal cobra 70 soles por envío")
        self.red.fallas = 99
        hits = asyncio.run(search.search("¿Cuánto cobra Brontal por envío?", 5, publish=False))
        self.assertTrue(hits and all(h["solo_texto"] for h in hits))
        self.assertTrue(search.relevant(hits), "los que coinciden por palabras sirven")

        class Chat:
            async def chat_ex(self, messages, **kw):
                return "Brontal cobra 70 soles por envío [1].", {}

        with mock.patch.object(ask, "get_client", lambda: Chat()):
            r = asyncio.run(ask.ask("¿Cuánto cobra Brontal por envío?"))
        self.assertIn("70 soles", r["answer"])
        self.assertIn(search.SOLO_TEXTO, r["answer"])


class ReindexTest(Base):
    def test_solo_vectores_llena_la_tabla_nueva_sin_tocar_la_vieja(self):
        with db.connect() as c:
            c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {db.VEC_VIEJA} USING vec0(embedding float[4])")
            c.execute(f"DELETE FROM {db.VEC_VIEJA}")
            for i, texto in enumerate(["uno", "dos", "tres"]):
                c.execute("INSERT INTO events (id, created_at, kind, source, size, sha256, raw_path, status)"
                          " VALUES (?, '2026-11-01', 'text', 'p', 1, ?, '/x', 'processed')", (f"e{i}", f"h{i}"))
                cid = c.execute("INSERT INTO chunks (event_id, position, content) VALUES (?, 0, ?)", (f"e{i}", texto)).lastrowid
                c.execute("INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (cid, texto))
                c.execute(f"INSERT INTO {db.VEC_VIEJA} (rowid, embedding) VALUES (?, ?)", (cid, sqlite_vec.serialize_float32([0.1] * 4)))
        with self.assertRaises(ValueError):
            reindex.borrar_vieja()  # la nueva aún está vacía
        with mock.patch.object(reindex.backup, "backup_now", self._respaldo):
            asyncio.run(self._correr())
        e = reindex.estado_vectores()
        self.assertEqual((e["trozos"], e["con_vector"], e["completo"]), (3, 3, True))
        self.assertEqual(e["tabla_vieja"], db.VEC_VIEJA, "la vieja sigue hasta comprobar")
        self.assertEqual(reindex.progreso["estado"], "listo")
        e = reindex.borrar_vieja()
        self.assertIsNone(e["tabla_vieja"])
        self.assertEqual(e["trozos"], 3, "los trozos no cambian")

    async def _respaldo(self):
        return {"snapshot": "prueba.db"}

    async def _correr(self):
        await reindex.solo_vectores()
        await reindex._tarea


if __name__ == "__main__":
    unittest.main()
