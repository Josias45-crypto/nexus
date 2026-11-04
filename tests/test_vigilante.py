"""Parte 2: recordatorios que no avisaron. Buzón con acuse y reintentos, programador aparte de los
saludos, marca de vida, vigilante y /health.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/v -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_vigilante
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from core import auth, clock, db, outbox, reminders, worker

AHORA = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("reminders", "outbox", "kv", "pending_confirmations"):
                c.execute(f"DELETE FROM {t}")
        self.ahora = AHORA
        p = mock.patch.object(clock, "now_utc", lambda: self.ahora)
        p.start()
        self.addCleanup(p.stop)

    def crear(self, minutos: float, texto: str = "revisar la caja") -> int:
        return reminders.create(texto, (self.ahora + timedelta(minutes=minutos)).isoformat(), "telegram", "1001")["id"]

    def buzon(self) -> list[dict]:
        with db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbox ORDER BY id")]


class BuzonTest(Base):
    def test_entregado_solo_con_acuse(self):
        with db.connect() as c:
            mid = outbox.put(c, "telegram", "1001", "reminder", "1", "⏰ Hora de revisar la caja")
        self.assertFalse(outbox.delivered(mid, ""), "sin message_id no cuenta")
        self.assertEqual(len(outbox.pending("telegram")), 1)
        self.assertTrue(outbox.delivered(mid, "4521"))
        self.assertEqual(self.buzon()[0]["acuse"], "4521")
        self.assertFalse(outbox.delivered(mid, "4522"), "no se entrega dos veces")

    def test_reintento_con_espera_creciente(self):
        with db.connect() as c:
            mid = outbox.put(c, "telegram", "1001", "reminder", "1", "aviso")
        esperas = [outbox.fallo(mid, "400")["reintento_s"] for _ in range(4)]
        self.assertEqual(esperas, [15, 30, 60, 120])
        self.assertEqual(outbox.pending("telegram"), [], "espera su turno")
        self.ahora += timedelta(seconds=121)
        self.assertEqual(len(outbox.pending("telegram")), 1)
        self.assertEqual(outbox.fallo(mid, "429", 7)["reintento_s"], 7, "respeta el retry_after de Telegram")
        for _ in range(10):
            r = outbox.fallo(mid, "403 bloqueado")
        self.assertEqual(r["reintento_s"], outbox.REINTENTO_MAX_S, "con tope")


class ProgramadorTest(Base):
    def test_proximo_y_atrasados(self):
        a = self.crear(10)
        self.crear(45)
        self.assertEqual(reminders.proximo(), self.ahora + timedelta(minutes=10))
        self.ahora += timedelta(minutes=11)
        self.assertEqual(reminders.atrasados(), [], "1 min de atraso aún no cuenta")
        self.ahora += timedelta(minutes=2)
        self.assertEqual(reminders.atrasados(), [a])

    def test_el_vigilante_reenvia_lo_atrasado_y_avisa(self):
        a = self.crear(5)
        self.ahora += timedelta(minutes=9)  # el programador no corrió: 4 min tarde

        async def una_vuelta():
            worker._tareas["programador"] = asyncio.create_task(asyncio.sleep(3600))  # vivo
            worker._latido = __import__("time").monotonic()
            try:
                return await worker.vigilar_una_vez()
            finally:
                worker._tareas["programador"].cancel()

        hecho = asyncio.run(una_vuelta())
        self.assertEqual((hecho["ids"], hecho["reenviados"]), ([a], 1))
        b = self.buzon()
        aviso = next(m for m in b if m["kind"] == "reminder")
        self.assertIn("retrasado", aviso["text"].lower())
        self.assertTrue(any(m["kind"] == "alerta" and "atrasaron" in m["text"] for m in b))
        self.assertEqual(reminders.atrasados(), [])

    def test_el_vigilante_reinicia_un_programador_muerto(self):
        async def una_vuelta():
            muerto = asyncio.create_task(asyncio.sleep(0))
            await muerto
            worker._tareas["programador"] = muerto
            hecho = await worker.vigilar_una_vez()
            nuevo = worker._tareas["programador"]
            vivo = not nuevo.done()
            nuevo.cancel()
            return hecho, vivo

        hecho, vivo = asyncio.run(una_vuelta())
        self.assertTrue(hecho.get("reinicio"))
        self.assertTrue(vivo)

    def test_sin_duplicados_aunque_corran_dos_a_la_vez(self):
        self.crear(1)
        self.ahora += timedelta(minutes=2)
        r1, r2 = reminders.due_step(), reminders.due_step()
        self.assertEqual(r1["avisos"] + r2["avisos"], 1)
        self.assertEqual(len([m for m in self.buzon() if m["kind"] == "reminder"]), 1)


class HealthTest(Base):
    def test_health_trae_recordatorios_atrasados(self):
        from core.main import app

        d = TestClient(app).get("/health", headers={auth.CABECERA: auth.token_interno()}).json()
        self.assertIn("recordatorios_atrasados", d)
        self.assertIn("programador", d)


if __name__ == "__main__":
    unittest.main()
