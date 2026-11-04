"""Recordatorios con seguimiento (Fase 2C) con reloj simulado y sin red ni IA.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/r -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_recordatorios
"""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import clock, conversa, db, persona, reminders

CANAL, CHAT = "simulado", "1001"
# Lunes 2 de noviembre de 2026, 10:00 en Perú (15:00 UTC)
INICIO = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


class Reloj(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("reminders", "outbox", "pending_confirmations", "charla"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM kv")
        self.ahora = INICIO
        p = mock.patch.object(clock, "now_utc", lambda: self.ahora)
        p.start()
        self.addCleanup(p.stop)
        persona.reiniciar()
        no_ia = mock.patch("core.conversa.get_client", side_effect=AssertionError("no debe usar IA"))
        no_ia.start()
        self.addCleanup(no_ia.stop)

    # ---------- utilidades ----------

    def avanza(self, minutos: float) -> int:
        self.ahora += timedelta(minutes=minutos)
        return reminders.due_step()["avisos"]

    def crear(self, minutos: float, texto: str = "llamar a Piter", regla=None) -> dict:
        due = self.ahora + timedelta(minutes=minutos)
        return reminders.create(texto, due.isoformat(), CANAL, CHAT, regla=regla)

    def avisos(self, rid: int | None = None) -> list[dict]:
        with db.connect() as c:
            rows = c.execute("SELECT text, botones, ref_id FROM outbox WHERE kind = 'reminder' ORDER BY id").fetchall()
        return [dict(r) for r in rows if rid is None or r["ref_id"] == str(rid)]

    def estado(self, rid: int) -> str:
        return reminders.get(rid)["estado"]

    def decir(self, texto: str) -> dict:
        return asyncio.run(conversa.conversar([texto], CANAL, CHAT))

    def pulsar(self, dato: str) -> dict:
        return asyncio.run(conversa.boton(dato, CANAL, CHAT))


class FlujoTest(Reloj):
    def test_previo_hora_tres_insistencias_y_sin_confirmar(self):
        r = self.crear(120)
        self.assertEqual(self.avanza(0), 0)
        self.assertEqual(self.avanza(89), 0)
        self.assertEqual(self.avanza(1), 1)  # 30 min antes
        self.assertEqual(self.estado(r["id"]), "avisado_previo")
        self.assertEqual(self.avanza(30), 1)  # a la hora
        self.assertEqual(self.estado(r["id"]), "avisado")
        for n, estado in ((1, "esperando"), (2, "esperando"), (3, "sin_confirmar")):
            self.assertEqual(self.avanza(19), 0, f"antes de la insistencia {n}")
            self.assertEqual(self.avanza(1), 1, f"insistencia {n}")
            self.assertEqual(self.estado(r["id"]), estado)
        self.assertEqual(self.avanza(600), 0, "tras la tercera ya no insiste")
        avisos = self.avisos(r["id"])
        self.assertEqual(len(avisos), 5)
        self.assertIsNone(avisos[0]["botones"], "el previo va sin botones")
        for a in avisos[1:]:
            self.assertIn('"Ya lo hice"', a["botones"])
            self.assertIn('"Recuérdame"', a["botones"])
            self.assertIn("llamar a Piter", a["text"])
        self.assertEqual(len({a["text"] for a in avisos}), 5, "cada aviso con su texto")

    def test_sin_previo_si_falta_poco(self):
        r = self.crear(25)
        self.assertEqual(self.avanza(24), 0)
        self.assertEqual(self.avanza(1), 1)
        self.assertEqual(self.estado(r["id"]), "avisado")
        self.assertIsNotNone(self.avisos(r["id"])[0]["botones"])

    def test_ya_lo_hice_en_cada_punto(self):
        for pasos, desc in ((90, "tras el previo"), (120, "a la hora"), (160, "tras 2 insistencias"),
                            (180, "sin confirmar")):
            with self.subTest(desc):
                self.setUp()
                r = self.crear(120)
                for m in range(10, pasos + 1, 10):
                    self.ahora = INICIO + timedelta(minutes=m)
                    reminders.due_step()
                antes = len(self.avisos(r["id"]))
                respuesta = self.pulsar(f"rec:hecho:{r['id']}")
                self.assertEqual(self.estado(r["id"]), "hecho")
                self.assertIn("llamar a Piter", respuesta["texto"])
                self.assertEqual(self.avanza(300), 0)
                self.assertEqual(len(self.avisos(r["id"])), antes, "se detiene al instante")

    def test_ya_lo_hice_por_texto_y_doble_clic(self):
        r = self.crear(10)
        self.avanza(10)
        self.assertIn("llamar a Piter", self.decir("ya lo hice")["texto"])
        self.assertEqual(self.estado(r["id"]), "hecho")
        self.assertIn("ya no está activo", self.pulsar(f"rec:hecho:{r['id']}")["texto"])

    def test_cancelar_detiene(self):
        r = self.crear(10)
        self.avanza(10)
        self.pulsar(f"rec:cancelar:{r['id']}")
        self.assertEqual(self.estado(r["id"]), "cancelado")
        self.assertEqual(self.avanza(100), 0)

    def test_cancelar_por_texto_pide_confirmacion(self):
        r = self.crear(60)
        self.assertIn("¿Cancelo", self.decir(f"cancela el {r['id']}")["texto"])
        self.assertEqual(self.estado(r["id"]), "creado")
        self.decir("sí")
        self.assertEqual(self.estado(r["id"]), "cancelado")


class PosponerTest(Reloj):
    def test_recuerdame_pregunta_cuando_y_entiende(self):
        r = self.crear(10)
        self.avanza(10)
        respuesta = self.pulsar(f"rec:posponer:{r['id']}")
        self.assertIn("?", respuesta["texto"])
        respuesta = self.decir("mañana a las 9 de la mañana")
        self.assertIn("9:00 a. m.", respuesta["texto"])
        nuevo = reminders.get(r["id"])
        self.assertEqual((nuevo["estado"], nuevo["due_local"]), ("pospuesto", "2026-11-03 09:00"))
        self.assertEqual(self.avanza(60), 0, "posponer detiene las insistencias")
        # vuelve a empezar: previo 8:30 y aviso 9:00 del día siguiente
        self.ahora = datetime(2026, 11, 3, 13, 30, tzinfo=timezone.utc)
        self.assertEqual(reminders.due_step()["avisos"], 1)
        self.assertEqual(self.estado(r["id"]), "avisado_previo")

    def test_en_un_dia_y_el_viernes(self):
        for frase, esperado in (("en un día", "2026-11-03 10:"), ("el viernes a las 3 pm", "2026-11-06 15:00")):
            with self.subTest(frase):
                self.setUp()
                r = self.crear(10)
                self.avanza(10)
                self.pulsar(f"rec:posponer:{r['id']}")
                self.decir(frase)
                self.assertTrue(reminders.get(r["id"])["due_local"].startswith(esperado), reminders.get(r["id"])["due_local"])

    def test_hora_no_clara_repregunta_con_botones(self):
        r = self.crear(10)
        self.avanza(10)
        self.pulsar(f"rec:posponer:{r['id']}")
        respuesta = self.decir("cuando puedas")
        self.assertTrue(respuesta["botones"], "repregunta con botones")
        self.assertEqual(self.estado(r["id"]), "avisado")
        self.pulsar(f"rec:pos:{r['id']}:1h")
        nuevo = reminders.get(r["id"])
        self.assertEqual(nuevo["estado"], "pospuesto")
        self.assertEqual(nuevo["due_local"], "2026-11-02 11:10")

    def test_pospon_el_n_una_hora(self):
        r = self.crear(10)
        respuesta = self.decir(f"pospón el {r['id']} una hora")
        self.assertIn("llamar a Piter", respuesta["texto"])
        self.assertEqual(reminders.get(r["id"])["due_local"], "2026-11-02 11:00")


class RepeticionTest(Reloj):
    def test_diaria_cierra_solo_la_actual(self):
        self.decir("recuérdame todos los días a las 7 am tomar agua")
        r = reminders.list_open(CHAT)[0]
        self.assertEqual((r["due_local"], r["regla"]), ("2026-11-03 07:00", {"tipo": "diaria"}))
        self.ahora = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)  # 7:00 en Perú
        reminders.due_step()
        self.decir("ya lo hice")
        abiertos = reminders.list_open(CHAT)
        self.assertEqual([x["due_local"] for x in abiertos], ["2026-11-04 07:00"])
        self.assertEqual(abiertos[0]["serie_id"], r["serie_id"])

    def test_semanal(self):
        self.decir("recuérdame todos los lunes a las 8 am sacar la basura")
        r = reminders.list_open(CHAT)[0]
        self.assertEqual(r["due_local"], "2026-11-09 08:00", "hoy lunes a las 10 ya pasaron las 8: el próximo lunes")
        reminders.cerrar(r["id"], "hecho", CHAT)
        self.assertEqual(reminders.list_open(CHAT)[0]["due_local"], "2026-11-16 08:00")

    def test_mensual_dia_31(self):
        self.decir("recuérdame el 31 de cada mes a las 9 am pagar el alquiler")
        r = reminders.list_open(CHAT)[0]
        self.assertEqual(r["due_local"], "2026-11-30 09:00", "noviembre no tiene 31")
        reminders.cerrar(r["id"], "hecho", CHAT)
        self.assertEqual(reminders.list_open(CHAT)[0]["due_local"], "2026-12-31 09:00")

    def test_sin_confirmar_crea_la_siguiente(self):
        r = self.crear(10, regla={"tipo": "diaria"})
        for _ in range(5):
            self.avanza(20)
        self.assertEqual(self.estado(r["id"]), "sin_confirmar")
        self.assertEqual(len(reminders.list_open(CHAT)), 1)

    def test_cancelar_serie_pregunta_solo_este_o_toda(self):
        r = self.crear(60, regla={"tipo": "semanal", "dia": 0})
        respuesta = self.pulsar(f"rec:cancelar:{r['id']}")
        self.assertEqual([b["texto"] for b in respuesta["botones"][0]], ["Solo este", "Toda la serie"])
        self.pulsar(f"rec:uno:{r['id']}")
        self.assertEqual(self.estado(r["id"]), "cancelado")
        siguiente = reminders.list_open(CHAT)
        self.assertEqual(len(siguiente), 1, "solo este: sigue la serie")
        self.pulsar(f"rec:cancelar:{siguiente[0]['id']}")
        self.pulsar(f"rec:serie:{siguiente[0]['id']}")
        self.assertEqual(reminders.list_open(CHAT), [], "toda la serie: no queda nada")


class FiabilidadTest(Reloj):
    def test_sin_duplicados_y_reinicio_a_mitad(self):
        r = self.crear(10)
        self.ahora += timedelta(minutes=10)
        self.assertEqual(reminders.due_step()["avisos"], 1)
        self.assertEqual(reminders.due_step()["avisos"], 0, "dos pasadas a la vez: un solo aviso")
        # «reinicio»: nada vive en memoria; el estado sigue en la base
        self.assertEqual(self.avanza(20), 1)
        self.assertEqual(reminders.get(r["id"])["insistencias"], 1)
        self.assertEqual(len(self.avisos(r["id"])), 2)

    def test_vencido_sale_retrasado_y_espaciado(self):
        r = self.crear(60)
        self.assertEqual(self.avanza(300), 1, "estuvo caído: un solo aviso, no todos de golpe")
        aviso = self.avisos(r["id"])[0]
        self.assertTrue(aviso["text"].startswith(persona.get()["recordatorios"]["retrasado"][0]), aviso["text"])
        self.assertIsNotNone(aviso["botones"], "salta el previo y avisa a la hora")
        self.assertEqual(self.avanza(1), 0)
        self.assertEqual(self.avanza(19), 1, "la insistencia, 20 min después")

    def test_silencio_no_aplica_a_lo_programado(self):
        self.ahora = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)  # 1:00 en Perú
        r = self.crear(60)  # 2:00 en Perú
        self.avanza(30)
        self.avanza(30)
        self.assertEqual(self.estado(r["id"]), "avisado")
        self.assertEqual(len(self.avisos(r["id"])), 2)  # previo 1:30 y aviso 2:00


class CrearTest(Reloj):
    def test_hora_clara_sin_preguntar_con_botones(self):
        respuesta = self.decir("recuérdame mañana a las 9 am llamar a Pedro")
        self.assertIn("9:00 a. m.: llamar a Pedro", respuesta["texto"])
        self.assertEqual([b["texto"] for b in respuesta["botones"][0]], ["Cambiar", "Cancelar"])

    def test_hora_dudosa_pregunta_con_botones(self):
        respuesta = self.decir("recuérdame a las 9 am revisar caja")  # hoy 10:00: ya pasó
        self.assertEqual([b["texto"] for b in respuesta["botones"][0]], ["Sí", "No, otra hora"])
        self.assertEqual(reminders.list_open(CHAT), [])
        respuesta = self.pulsar("pend:otra")
        self.assertIn("¿A qué hora", respuesta["texto"])
        self.decir("a las 11 am")
        self.assertEqual(reminders.list_open(CHAT)[0]["due_local"], "2026-11-02 11:00")

    def test_dudosa_si_por_boton(self):
        self.decir("recuérdame a las 9 am revisar caja")
        self.pulsar("pend:si")
        self.assertEqual(reminders.list_open(CHAT)[0]["due_local"], "2026-11-03 09:00")

    def test_lista_con_estado_y_hora_de_peru(self):
        r = self.crear(10)
        self.avanza(10)
        self.crear(60, "pagar la luz", regla={"tipo": "mensual", "dia": 15})
        texto = self.decir("¿qué tengo pendiente?")["texto"]
        self.assertIn("hora de Perú", texto)
        self.assertIn(f"n.º {r['id']}", texto)
        self.assertIn("esperando su confirmación", texto)
        self.assertIn("el 15 de cada mes", texto)

    def test_textos_sin_repetir_la_variante_anterior(self):
        with mock.patch.object(persona, "tratamiento", lambda: "jefe"):
            textos = [persona.frase("recordatorios.hora", tarea="x", hora="y") for _ in range(20)]
        for a, b in zip(textos, textos[1:]):
            self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(set(textos)), 3, "usa varias variantes")


class MigracionTest(unittest.TestCase):
    def test_migracion_no_destructiva(self):
        ruta = Path(tempfile.mkdtemp()) / "vieja.db"
        con = sqlite3.connect(ruta)
        con.row_factory = sqlite3.Row
        con.executescript(
            "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,"
            " due_at TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', channel TEXT,"
            " chat_id TEXT, source_event_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_sent_at TEXT, done_at TEXT);"
            "INSERT INTO reminders (created_at, due_at, text, status, attempts, last_sent_at) VALUES"
            " ('2026-10-01T00:00:00+00:00', '2026-12-01T14:00:00+00:00', 'a', 'pending', 0, NULL),"
            " ('2026-10-01T00:00:00+00:00', '2026-10-02T14:00:00+00:00', 'b', 'sent', 2, '2026-10-02T14:30:00+00:00'),"
            " ('2026-10-01T00:00:00+00:00', '2026-10-02T14:00:00+00:00', 'c', 'done', 1, NULL),"
            " ('2026-10-01T00:00:00+00:00', '2026-10-02T14:00:00+00:00', 'd', 'cancelled', 0, NULL);"
        )
        db._migrate_reminders(con)
        db._migrate_reminders(con)  # dos veces: no rompe
        rows = {r["text"]: dict(r) for r in con.execute("SELECT * FROM reminders")}
        self.assertEqual([rows[k]["estado"] for k in "abcd"], ["creado", "esperando", "hecho", "cancelado"])
        self.assertEqual(rows["a"]["proximo_envio"], "2026-12-01T14:00:00+00:00")
        self.assertEqual(rows["b"]["proximo_envio"], "2026-10-02T14:50:00+00:00")
        self.assertEqual(rows["b"]["insistencias"], 1)
        self.assertEqual(rows["b"]["status"], "sent", "lo viejo no se borra")


class HoraAmbiguaTest(Reloj):
    """Hora sin am/pm: se pregunta por texto (sin botones), salvo una sola vez en menos de 2 h."""

    def abiertos(self):
        return reminders.list_open(CHAT)

    def pregunta(self, texto: str) -> dict:
        r = self.decir(texto)
        self.assertIsNone(r["botones"], "la pregunta de am/pm va sin botones")
        self.assertTrue(r["texto"].startswith("¿") and r["texto"].endswith("?"), r["texto"])
        self.assertIsNotNone(reminders.pregunta_hora(CANAL, CHAT), "queda en la base")
        self.assertEqual(self.abiertos(), [])
        return r

    def test_cada_dia_a_las_7_pregunta_y_de_la_noche(self):
        r = self.pregunta("recuérdame cada día a las 7 tomar la pastilla")
        self.assertIn("Las 7 de la mañana o de la noche", r["texto"])
        r = self.decir("de la noche")
        [rec] = self.abiertos()
        self.assertEqual(rec["regla"], {"tipo": "diaria"})
        self.assertEqual(rec["due_local"], "2026-11-02 19:00")
        self.assertIn("7:00 p. m.", r["texto"])
        self.assertIn("todos los días", r["texto"])
        self.assertIsNone(reminders.pregunta_hora(CANAL, CHAT))

    def test_respuestas_escritas(self):
        casos = {"de la mañana": "08:00", "am": "08:00", "en la mañana": "08:00", "a las 8 pm": "20:00",
                 "8:30 p. m.": "20:30", "20:00": "20:00", "8pm por favor": "20:00", "de la noche": "20:00"}
        for respuesta, hora in casos.items():
            with self.subTest(respuesta=respuesta):
                with db.connect() as c:
                    c.execute("DELETE FROM reminders")
                self.pregunta("recuérdame mañana a las 8 pagar la luz")
                r = self.decir(respuesta)
                [rec] = self.abiertos()
                self.assertEqual(rec["due_local"], f"2026-11-03 {hora}", respuesta)
                self.assertTrue(" a. m." in r["texto"] or " p. m." in r["texto"], r["texto"])

    def test_repetidos_siempre_preguntan_aunque_falte_poco(self):
        self.ahora = INICIO + timedelta(minutes=50)  # 10:50: «a las 11» cae en 10 min
        self.pregunta("recuérdame todos los lunes a las 11 revisar caja")
        self.decir("am")
        [rec] = self.abiertos()
        self.assertEqual((rec["due_local"], rec["regla"]), ("2026-11-02 11:00", {"tipo": "semanal", "dia": 0}))

    def test_una_vez_en_menos_de_2_horas_se_toma(self):
        r = self.decir("recuérdame a las 11 llamar a Piter")  # son las 10:00
        self.assertIn("11:00 a. m.", r["texto"])
        [rec] = self.abiertos()
        self.assertEqual(rec["due_local"], "2026-11-02 11:00")
        self.assertIsNone(reminders.pregunta_hora(CANAL, CHAT))

    def test_una_vez_a_mas_de_2_horas_pregunta(self):
        r = self.pregunta("recuérdame a las 7 llamar a Piter")  # 7 p. m. queda a 9 h
        self.assertIn("Las 7 de la mañana o de la noche", r["texto"])
        self.decir("de la noche")
        self.assertEqual(self.abiertos()[0]["due_local"], "2026-11-02 19:00")

    def test_de_la_manana_que_ya_paso_es_manana(self):
        self.pregunta("recuérdame a las 7 llamar a Piter")
        self.decir("de la mañana")
        self.assertEqual(self.abiertos()[0]["due_local"], "2026-11-03 07:00")

    def test_contexto_resuelve_sin_preguntar(self):
        for texto, hora in (("recuérdame esta noche a las 8 cerrar caja", "2026-11-02 20:00"),
                            ("recuérdame mañana por la tarde a las 4 ir al banco", "2026-11-03 16:00"),
                            ("recuérdame mañana a las 9 de la mañana llamar", "2026-11-03 09:00"),
                            ("recuérdame a las 18 pagar", "2026-11-02 18:00"),
                            ("recuérdame mañana al mediodía almorzar con Ana", "2026-11-03 12:00")):
            with self.subTest(texto=texto):
                with db.connect() as c:
                    c.execute("DELETE FROM reminders")
                r = self.decir(texto)
                self.assertIsNone(reminders.pregunta_hora(CANAL, CHAT), texto)
                self.assertEqual(self.abiertos()[0]["due_local"], hora, r["texto"])

    def test_si_no_responde_se_descarta_y_sigue_normal(self):
        self.pregunta("recuérdame a las 7 llamar a Piter")
        r = self.decir("¿qué recordatorios tengo?")  # otra cosa: no se la traga
        self.assertEqual(r["accion"], "recordatorios")
        self.assertIsNone(reminders.pregunta_hora(CANAL, CHAT), "pregunta descartada")
        self.assertEqual(self.abiertos(), [])

    def test_otro_recordatorio_en_vez_de_responder(self):
        self.pregunta("recuérdame a las 7 llamar a Piter")
        r = self.decir("recuérdame mañana a las 9 am pagar la luz")
        self.assertEqual([x["text"] for x in self.abiertos()], ["pagar la luz"], r["texto"])

    def test_la_pregunta_caduca(self):
        self.pregunta("recuérdame a las 7 llamar a Piter")
        self.ahora += timedelta(minutes=reminders.PREGUNTA_HORA_MIN + 1)
        self.assertIsNone(reminders.pregunta_hora(CANAL, CHAT))

    def test_sobrevive_reinicio(self):
        self.pregunta("recuérdame a las 7 llamar a Piter")
        persona.get.cache_clear()  # el estado vive en la base, no en memoria
        self.decir("pm")
        self.assertEqual(self.abiertos()[0]["due_local"], "2026-11-02 19:00")

    def test_cambiar_hora_ambigua_pregunta_y_conserva_el_dia(self):
        rec = self.crear(60 * 24)  # mañana 10:00
        self.pulsar(f"rec:cambiar:{rec['id']}")
        r = self.decir("mejor a las 9")
        self.assertIsNone(r["botones"])
        self.assertIn("Las 9 de la mañana o de la noche", r["texto"])
        self.decir("de la mañana")
        self.assertEqual(reminders.get(rec["id"])["due_local"], "2026-11-03 09:00")

    def test_respuesta_ampm_unitaria(self):
        from core import when
        from datetime import time
        self.assertEqual(when.respuesta_ampm("de la noche", 7, 0), time(19, 0))
        self.assertEqual(when.respuesta_ampm("De la mañana, Isabelle", 7, 0), time(7, 0))
        self.assertEqual(when.respuesta_ampm("a las 7:30 pm", 7, 0), time(19, 30))
        self.assertEqual(when.respuesta_ampm("medianoche", 12, 0), time(0, 0))
        self.assertEqual(when.respuesta_ampm("del mediodía", 12, 0), time(12, 0))
        for otra in ("esta noche voy al cine con Ana", "¿qué tengo mañana?", "llama a Rosa", "sí", "7"):
            self.assertIsNone(when.respuesta_ampm(otra, 7, 0), otra)


if __name__ == "__main__":
    unittest.main()
