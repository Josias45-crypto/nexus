"""Fase 2E sin red: búsqueda en internet (IA falsa) y «olvida eso» con borrado en cascada.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/b -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_busqueda_olvido
"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from config import settings
from core import ask, charla, clock, conversa, db, inbox, internet, memory, olvido, pendientes, persona, reminders

CANAL, CHAT = "simulado", "1001"
AHORA = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)  # martes 10:00 en Perú
FUENTES = [
    {"url": "https://www.sunat.gob.pe/tipo-cambio", "titulo": "Tipo de cambio oficial | SUNAT", "extracto": "El tipo de cambio hoy es 3,75 soles por dólar."},
    {"url": "https://www.bcrp.gob.pe/x", "titulo": "Banco Central de Reserva del Perú - BCRP", "extracto": "Cotización 3,75."},
]


class IAFalsa:
    """Responde según el tipo de llamada: búsqueda web, saber general o lo demás."""

    def __init__(self, general="NO_SE", web="Según SUNAT, el tipo de cambio hoy es 3,75 soles por dólar.", fuentes=FUENTES):
        self.general, self.web, self.fuentes = general, web, fuentes
        self.llamadas: list[dict] = []

    async def chat_ex(self, messages, **kw):
        self.llamadas.append({"messages": messages, **kw})
        if kw.get("web"):
            return self.web, {"fuentes": self.fuentes, "costo_usd": 0.0081, "proveedor": "openrouter-internet"}
        if "NO_SE" in messages[0]["content"]:
            return self.general, {}
        return "Claro.", {}

    async def chat(self, messages, **kw):
        return (await self.chat_ex(messages, **kw))[0]


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("reminders", "outbox", "pending_confirmations", "charla", "kv", "busquedas", "olvidos", "digests"):
                c.execute(f"DELETE FROM {t}")
            for r in c.execute("SELECT id FROM events").fetchall():
                memory.delete_chunks(c, r["id"])
            c.execute("DELETE FROM events")
        persona.get.cache_clear()
        persona.reiniciar()
        self.ahora = AHORA
        for objetivo in (mock.patch.object(clock, "now_utc", lambda: self.ahora),):
            objetivo.start()
            self.addCleanup(objetivo.stop)
        self.ia = IAFalsa()
        for donde in ("core.conversa.get_client", "core.internet.get_client"):
            p = mock.patch(donde, lambda: self.ia)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ask, "ask", self._sin_memoria)
        p.start()
        self.addCleanup(p.stop)
        self.interpretado = {"accion": "preguntar", "consulta": ""}
        p = mock.patch.object(conversa, "interpretar", self._interpretar)
        p.start()
        self.addCleanup(p.stop)

    async def _sin_memoria(self, pregunta, **kw):
        return {"answer": ask.NO_INFO, "sources": []}

    async def _interpretar(self, texto, previos):
        return {**self.interpretado, "consulta": self.interpretado["consulta"] or texto}

    def decir(self, texto: str) -> dict:
        return asyncio.run(conversa.conversar([texto], CANAL, CHAT))

    def seguir(self) -> dict:
        return asyncio.run(conversa.seguir(CANAL, CHAT))

    def nota(self, texto: str, nombre: str = "nota.txt") -> str:
        r = inbox.save(texto.encode(), nombre, "text/plain", "prueba", kind="text")
        with db.connect() as c:
            memory.insert_chunk(c, r["id"], 0, texto, [0.0] * settings.EMBED_DIM)
            c.execute("UPDATE events SET status = 'processed' WHERE id = ?", (r["id"],))
            c.execute("INSERT INTO digests (event_id, status, summary, created_at) VALUES (?, 'done', ?, ?)",
                      (r["id"], texto[:40], self.ahora.isoformat())) if self._digest_ok() else None
        return r["id"]

    @staticmethod
    def _digest_ok() -> bool:
        with db.connect() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(digests)")}
        return {"event_id", "status", "summary", "created_at"} <= cols


class BusquedaTest(Base):
    def test_pedida_avisa_aparte_y_luego_busca_con_fuentes(self):
        r = self.decir("busca en internet el tipo de cambio del dólar hoy")
        self.assertTrue(r["seguir"])
        self.assertIn("internet", r["texto"])
        self.assertEqual(self.ia.llamadas, [], "el aviso sale antes de gastar nada")
        r = self.seguir()
        self.assertEqual(len(self.ia.llamadas), 1)
        self.assertTrue(self.ia.llamadas[0]["web"])
        self.assertIn("Fuentes de internet: SUNAT (sunat.gob.pe); BCRP (bcrp.gob.pe).", r["texto"])
        self.assertIn("¿Quiere que lo guarde en su cerebro", r["texto"])
        self.assertEqual(r["nivel"], "internet")
        self.assertEqual(internet.hechas_hoy(), 1)
        self.assertEqual(self.seguir()["texto"], "", "la búsqueda anunciada se hace una sola vez")

    def test_niveles_cerebro_general_internet(self):
        # General y sabido: responde sin internet y avisa que no sale del cerebro
        self.ia.general = "El CPC es el costo por clic."
        r = self.decir("¿qué es el CPC en publicidad?")
        self.assertFalse(r["seguir"])
        self.assertEqual(r["nivel"], "general")
        self.assertFalse(any(l.get("web") for l in self.ia.llamadas))
        # Reciente o local: internet sin preguntarle al modelo
        self.ia.llamadas.clear()
        r = self.decir("¿cuál es el precio del dólar hoy en Lima?")
        self.assertTrue(r["seguir"])
        self.assertEqual(self.ia.llamadas, [], "regla barata: sin IA antes del aviso")
        self.seguir()
        # El modelo no lo sabe (NO_SE): internet
        self.ia.general = "NO_SE"
        self.assertTrue(self.decir("¿quién fundó la cadena Zorvak de ferreterías?")["seguir"])
        pendientes.tomar(CANAL, CHAT, "busqueda")
        # «No busques en internet» se respeta en esa pregunta
        r = self.decir("¿quién fundó la cadena Zorvak? no busques en internet")
        self.assertFalse(r["seguir"])
        self.assertIn("me pidió no buscar", r["texto"])
        r = self.decir("no busques en internet, ¿cuál es el precio del dólar hoy?")
        self.assertFalse(r["seguir"])

    def test_sobre_sus_datos_no_va_a_internet(self):
        self.ia.general = "NO_ANOTADO No lo tengo anotado."
        r = self.decir("¿cuánto me debe Rosa hoy?")
        self.assertFalse(r["seguir"])
        self.assertFalse(any(l.get("web") for l in self.ia.llamadas))

    def test_tope_diario(self):
        with db.connect() as c:
            for _ in range(20):
                c.execute("INSERT INTO busquedas (created_at, dia, fuentes) VALUES (?, '2026-11-03', 3)", (self.ahora.isoformat(),))
        r = self.decir("busca en internet el clima en Trujillo hoy")
        self.assertFalse(r["seguir"])
        self.assertIn("20 búsquedas", r["texto"])
        self.assertEqual("\n" in r["texto"], False, "una línea")
        self.ahora += timedelta(days=1)
        self.assertTrue(self.decir("busca en internet el clima de hoy")["seguir"], "al día siguiente sí")

    def test_consulta_sin_datos_personales(self):
        with db.connect() as c:
            charla._set(c, charla.K_FICHA, json.dumps({"personas": ["Rosa Quispe: clienta que debe"], "negocio": []}))
        self.nota("Tralvex Perú SAC entrega laptops los martes; contacto Marco Ruiz.")
        self.decir("busca en internet cómo cobrarle a Rosa Quispe los 350 soles que me debe, su número es 987 654 321 "
                   "y su correo rosa@mail.com, y si Marco Ruiz de Tralvex Perú SAC puede ser fiador")
        self.seguir()
        enviado = json.dumps(self.ia.llamadas[-1]["messages"], ensure_ascii=False)
        for privado in ("Rosa", "Quispe", "350", "987", "rosa@mail.com", "Marco Ruiz", "Tralvex Perú SAC", "Josias", "jefe"):
            self.assertNotIn(privado, enviado, privado)
        self.assertIn("cobrar", enviado)
        self.assertIsNone(internet.consulta_limpia("busca en internet a Rosa Quispe"))

    def test_fuentes_legibles(self):
        self.assertEqual(internet.sitio("Noticias | El Comercio", "https://elcomercio.pe/x"), ("El Comercio", "elcomercio.pe"))
        self.assertEqual(internet.sitio("Una página sin marca", "https://www.ejemplo.com.pe/a"), ("Ejemplo", "ejemplo.com.pe"))
        fs = internet.fuentes_legibles(FUENTES + FUENTES + [{"url": "https://a.com", "titulo": "A"}, {"url": "https://b.com", "titulo": "B"}])
        self.assertEqual([f["dominio"] for f in fs], ["sunat.gob.pe", "bcrp.gob.pe", "a.com"])

    def test_respuesta_y_fuentes_limpias(self):
        self.assertEqual(internet._limpiar_respuesta("Está a 3,75 soles, según [sunat.gob.pe](https://sunat.gob.pe/x)."),
                         "Está a 3,75 soles, según sunat.gob.pe.")
        fs = internet.fuentes_legibles([{"url": "https://exa.ai/x", "titulo": "As of 2026-11-03T19:07:12Z"}] + FUENTES)
        self.assertEqual(fs[0]["dominio"], "sunat.gob.pe")
        self.assertIn("«Rosa»", olvido._confirmacion({**olvido._plan([], "Rosa"), "termino": olvido._original("olvida lo de Rosa", "rosa")}))

    def test_verifica_datos_contra_lo_devuelto(self):
        self.ia.web = "Según SUNAT, el dólar está a 3,75 soles y el BCRP prevé 3,90 para marzo según Julio Velarde."
        self.decir("busca en internet el tipo de cambio del dólar")
        r = self.seguir()
        self.assertIn("⚠ Esto no lo vi en las fuentes", r["texto"])
        self.assertIn("3,90", r["texto"].split("⚠")[1])
        self.assertIn("Julio Velarde", r["texto"].split("⚠")[1])

    def test_sin_fuentes_no_ofrece_guardar(self):
        self.ia.fuentes = []
        self.decir("busca en internet algo rarísimo de ferreterías")
        r = self.seguir()
        self.assertIn("no encontré fuentes", r["texto"])
        self.assertFalse(pendientes.hay(CANAL, CHAT, "web"))


class GuardarTest(Base):
    def _buscado(self):
        self.decir("busca en internet el tipo de cambio del dólar hoy")
        self.seguir()

    def _notas_internet(self) -> list[str]:
        with db.connect() as c:
            return [Path(r["raw_path"]).read_text() for r in c.execute("SELECT raw_path FROM events WHERE source = 'internet'")]

    def test_si_guarda_con_fuente_y_fecha(self):
        self._buscado()
        r = self.decir("sí, guárdalo")
        self.assertIn("nota de internet", r["texto"])
        notas = self._notas_internet()
        self.assertEqual(len(notas), 1)
        self.assertIn("[De internet, consultado el 3 de noviembre de 2026]", notas[0])
        self.assertIn("sunat.gob.pe", notas[0])

    def test_no_no_guarda(self):
        self._buscado()
        self.assertIn("no lo guardo", self.decir("no gracias")["texto"])
        self.assertEqual(self._notas_internet(), [])

    def test_otro_mensaje_descarta_sin_tragarlo(self):
        self._buscado()
        r = self.decir("recuérdame mañana a las 9 am llamar a Piter")
        self.assertIn("llamar a Piter", r["texto"])
        self.assertEqual(self._notas_internet(), [])
        self.assertFalse(pendientes.hay(CANAL, CHAT, "web"))

    def test_caduca(self):
        self._buscado()
        self.ahora += timedelta(minutes=31)
        self.decir("sí")
        self.assertEqual(self._notas_internet(), [])


class OlvidoTest(Base):
    def setUp(self):
        super().setUp()
        self.respaldo = Path(tempfile.mkdtemp())
        p = mock.patch.object(settings, "BACKUP_DIR", str(self.respaldo))
        p.start()
        self.addCleanup(p.stop)
        self.rosa1 = self.nota("Rosa Quispe se llevó una impresora Epson y pagó 100 soles.")
        self.ahora += timedelta(minutes=1)
        self.rosa2 = self.nota("Rosa Quispe debe 350 soles y paga los viernes.")
        self.ahora += timedelta(minutes=1)
        self.tralvex = self.nota("Tralvex entrega las laptops los martes.")
        with db.connect() as c:
            charla._set(c, charla.K_FICHA, json.dumps({"personas": ["Rosa Quispe: clienta de impresoras", "Piter: proveedor"]}))
            charla._set(c, charla.K_RESUMEN, "Hablaron de la deuda de Rosa Quispe. También de abrir un local.")
            for rid in (self.rosa1, self.rosa2):  # su copia en el espejo de respaldos
                raw = Path(c.execute("SELECT raw_path FROM events WHERE id = ?", (rid,)).fetchone()["raw_path"])
                espejo = olvido._espejo(str(raw))
                espejo.parent.mkdir(parents=True, exist_ok=True)
                espejo.write_text("copia")
        charla.agregar("jefe", "Rosa Quispe me debe 350 soles", CHAT)
        charla.agregar("isabelle", "Anotado lo de Rosa.", CHAT)
        charla.agregar("jefe", "el local nuevo va en el centro", CHAT)
        self.rec = reminders.create("cobrar a Rosa", (self.ahora + timedelta(days=1)).isoformat(), CANAL, CHAT)

    def conteo(self, sql: str, *args) -> int:
        with db.connect() as c:
            return c.execute(sql, args).fetchone()[0]

    def test_ambiguo_lista_numerada_y_elige(self):
        r = self.decir("borra lo de Rosa")["texto"]
        self.assertIn("1. Nota del", r)
        self.assertIn("2. Nota del", r)
        self.assertIn("«la 2»", r)
        r = self.decir("la 2")["texto"]
        self.assertIn("¿Confirma que olvide", r)
        self.assertIn("Esto lo borra del todo", r)
        self.assertEqual(r.count("respaldos de los últimos 7 días"), 1)
        self.decir("sí")
        with db.connect() as c:
            ids = {r[0] for r in c.execute("SELECT id FROM events")}
        self.assertEqual(ids, {self.rosa2, self.tralvex}, "la 2 es la más antigua (lista de reciente a antigua)")

    def test_cascada_completa(self):
        r = self.decir("olvida todo lo de Rosa")["texto"]
        self.assertIn("¿Confirma que olvide", r)
        self.assertIn("«Rosa Quispe: clienta de impresoras»", r, "lo de la ficha se lista para confirmar")
        self.assertIn("«cobrar a Rosa»", r)
        with db.connect() as c:
            raws = [Path(x["raw_path"]) for x in c.execute("SELECT raw_path FROM events WHERE id IN (?, ?)", (self.rosa1, self.rosa2))]
        cierre = self.decir("sí")["texto"]
        self.assertIn("olvidé 2 notas", cierre)
        self.assertIn("cancelé 1 recordatorio ligado", cierre)
        self.assertEqual(cierre.count("respaldos de los últimos 7 días"), 1)
        q = "SELECT COUNT(*) FROM {} WHERE event_id IN (?, ?)"
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events WHERE id IN (?, ?)", self.rosa1, self.rosa2), 0)
        self.assertEqual(self.conteo(q.format("chunks"), self.rosa1, self.rosa2), 0)
        self.assertEqual(self.conteo(q.format("digests"), self.rosa1, self.rosa2), 0)
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'Rosa'"), 0)
        self.assertEqual(self.conteo(f"SELECT COUNT(*) FROM {db.VEC}"), self.conteo("SELECT COUNT(*) FROM chunks"))
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events"), 1, "Tralvex sigue")
        for raw in raws:
            self.assertFalse(raw.exists(), "original borrado")
            self.assertFalse(olvido._espejo(str(raw)).exists(), "copia del espejo borrada")
        self.assertEqual(charla.ficha()["personas"], ["Piter: proveedor"])
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM charla_fts WHERE charla_fts MATCH 'Rosa'"), 0)
        self.assertTrue(any("local nuevo" in m["texto"] for m in charla.recientes(50, CHAT)), "lo demás de la charla queda")
        self.assertEqual(charla.resumen(), "También de abrir un local.")
        self.assertEqual(reminders.get(self.rec["id"])["estado"], "cancelado")
        with db.connect() as c:
            tipos = sorted(r["tipo"] for r in c.execute("SELECT tipo FROM olvidos"))
            columnas = {r["name"] for r in c.execute("PRAGMA table_info(olvidos)")}
        self.assertEqual(columnas, {"id", "created_at", "tipo"}, "sin contenido")
        self.assertEqual(tipos.count("nota"), 2)
        self.assertIn("ficha", tipos)
        self.assertIn("charla", tipos)
        self.assertIn("recordatorio", tipos)
        self.assertIn("resumen", tipos)

    def test_solo_si_ejecuta(self):
        self.decir("elimina la nota de Tralvex")
        r = self.decir("ok")
        self.assertNotIn("olvidé", r["texto"])
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events"), 3)
        self.assertFalse(pendientes.hay(CANAL, CHAT, "olvido"), "otra respuesta descarta la pregunta")
        self.decir("elimina la nota de Tralvex")
        self.assertIn("no borro nada", self.decir("no")["texto"])
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events"), 3)
        self.decir("elimina la nota de Tralvex")
        self.decir("sí")
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events WHERE id = ?", self.tralvex), 0)
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events"), 2, "no borra más de lo pedido")

    def test_olvida_eso_es_lo_ultimo(self):
        r = self.decir("olvida eso")["texto"]
        self.assertIn("Tralvex", r)
        self.decir("sí")
        self.assertEqual(self.conteo("SELECT COUNT(*) FROM events WHERE id = ?", self.tralvex), 0)

    def test_mas_de_10_pide_acotar_y_nada_encontrado(self):
        for i in range(11):
            self.nota(f"Venta {i} a Zuleta: laptop número {i}.")
        r = self.decir("olvida todo lo de Zuleta")["texto"]
        self.assertIn("más de 10", r)
        self.assertFalse(pendientes.hay(CANAL, CHAT, "olvido"))
        self.assertIn("No encuentro nada", self.decir("borra lo de Wilfredo")["texto"])

    def test_no_choca_con_recordatorios(self):
        self.assertIsNone(olvido.pedir("borra el recordatorio de Rosa", CANAL, CHAT))


if __name__ == "__main__":
    unittest.main()
