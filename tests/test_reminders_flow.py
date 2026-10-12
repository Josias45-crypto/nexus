"""Recordatorios de punta a punta sin red: manejador real del canal + API real de NEXUS sobre una
base temporal (httpx.ASGITransport, sin el worker). Sin modelo: las fechas se entienden por reglas.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/x -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_reminders_flow
"""

import asyncio
import sqlite3
import unittest
from datetime import datetime
from unittest import mock

import httpx

from channels.assistant import Assistant
from channels.base import Intent, confirmation
from channels.client import NexusClient
from channels.simulado import OWNER, SimChannel
from core import db, reminders
from core.main import app

PEDIDO = "recuérdame mañana a las 9 llamar a Pedro"


def rows() -> int:
    with sqlite3.connect(db.DB_PATH) as c:
        return c.execute("SELECT count(*) FROM reminders").fetchone()[0]


class ConfirmationWordsTest(unittest.TestCase):
    def test_classify(self):
        cases = {
            "sí": Intent.YES, "si": Intent.YES, "Sí, guárdalo": Intent.YES, "ok": Intent.YES,
            "ok gracias": Intent.YES, "dale!": Intent.YES, "confirmo": Intent.YES,
            "si por favor": Intent.YES, "no": Intent.NO, "No, cancela": Intent.NO,
            "sí, ¿y a qué hora?": Intent.UNCLEAR, "si y a que hora": Intent.UNCLEAR,
            "si... no, espera": Intent.UNCLEAR, "quizás": Intent.UNCLEAR, "": Intent.UNCLEAR,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(confirmation(text), want)


class FlowTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            c.execute("DELETE FROM reminders")
            c.execute("DELETE FROM pending_confirmations")

    def bot(self) -> tuple[Assistant, SimChannel]:
        """Un bot nuevo = un reinicio: no hay nada en memoria."""
        ch = SimChannel([OWNER])
        nexus = NexusClient("http://nexus", transport=httpx.ASGITransport(app=app))
        return Assistant(ch, nexus, {OWNER}), ch

    def say(self, bot: Assistant, ch: SimChannel, text: str) -> str:
        return "\n".join(asyncio.run(bot.handle(ch.text(text))))

    def test_survives_restart_and_is_idempotent(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, PEDIDO)
        self.assertIn("¿Lo guardo?", r)
        self.assertEqual(rows(), 0)
        bot, ch = self.bot()  # reinicio entre la pregunta y el "sí"
        r = self.say(bot, ch, "Sí, guárdalo")
        self.assertIn("Guardado (n.º", r)
        self.assertIn("hora de Perú", r)
        self.assertEqual(rows(), 1)
        r = self.say(bot, ch, "sí")
        self.assertIn("Ya estaba guardado", r)
        self.assertEqual(rows(), 1)
        r = self.say(bot, ch, "/recordatorios")
        self.assertIn("llamar a Pedro", r)
        self.assertIn("[pendiente]", r)
        self.assertIn("hora de Perú", r)

    def test_parallel_yes_creates_one(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)

        async def both():
            return await asyncio.gather(bot.handle(ch.text("sí")), bot.handle(ch.text("ok")))

        asyncio.run(both())
        self.assertEqual(rows(), 1)

    def test_unclear_asks_again(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        for text in ("sí, ¿y a qué hora?", "quizás"):
            r = self.say(bot, ch, text)
            self.assertIn("No entendí tu respuesta", r)
            self.assertNotIn("Guardado", r)
        self.assertEqual(rows(), 0)
        r = self.say(bot, ch, "no")
        self.assertIn("no lo guardo", r)
        r = self.say(bot, ch, "sí")  # ya no hay nada pendiente: no guarda
        self.assertNotIn("Guardado", r)
        self.assertEqual(rows(), 0)

    def test_bad_date_asks(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "recuérdame algo sin fecha")
        self.assertNotIn("Guardado", r)
        self.assertEqual(rows(), 0)

    def test_probar_recordatorio(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "/probar_recordatorio")
        self.assertIn("Recordatorio de prueba n.º", r)
        self.assertEqual(rows(), 1)


    def test_probar_y_cancelar_por_chat(self):
        bot, ch = self.bot()
        self.say(bot, ch, "/probar_recordatorio")
        rid = reminders.list_open(OWNER)[0]["id"]
        # Otro chat autorizado no puede cancelarlo
        other = SimChannel(["2002"])
        bot2 = Assistant(other, NexusClient("http://nexus", transport=httpx.ASGITransport(app=app)), {"2002"})
        r = "\n".join(asyncio.run(bot2.handle(other.text(f"/cancelar {rid}", user="2002"))))
        self.assertNotIn("Cancelado", r)
        self.assertEqual(reminders.get(rid)["status"], "pending")
        r = self.say(bot, ch, f"/cancelar {rid}")
        self.assertIn(f"Cancelado (n.º {rid})", r)
        self.assertEqual(reminders.get(rid)["status"], "cancelled")
        self.assertIn("/cancelar y el número", self.say(bot, ch, "/cancelar"))


NOW_415 = datetime(2026, 10, 12, 4, 15)  # el fallo real: 04:15 en Perú


class TimeConfirmationTest(FlowTest):
    """Confirmación por el manejador real con la hora simulada (04:15)."""

    def setUp(self):
        super().setUp()
        p = mock.patch("core.clock.now_local", lambda: NOW_415)
        p.start()
        self.addCleanup(p.stop)

    def test_formato_hora_y_tarea(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "Recuerdame hoy a las 4 : 17 llamar a Piter")
        self.assertIn("Entendí: hoy 4:17 a. m. (04:17, hora de Perú)", r)
        self.assertIn("\nTarea: llamar a Piter\n", r)
        r = self.say(bot, ch, "sí")
        self.assertIn("a las 4:17 a. m. (04:17, hora de Perú)", r)
        self.assertIn("Tarea: llamar a Piter", r)
        r = self.say(bot, ch, "/recordatorios")
        self.assertIn("4:17 a. m. (04:17) — llamar a Piter [pendiente]", r)

    def test_hora_ambigua_pregunta(self):
        bot, ch = self.bot()
        self.say(bot, ch, "recuérdame hoy a las 4 y 18 llamar a Pedro")  # queda pendiente
        r = self.say(bot, ch, "recuérdame hoy a las 4 17 llamar a Piter")
        self.assertIn("¿A qué hora exactamente? Ejemplo: 4:17 pm o 16:17", r)
        self.assertNotIn("Entendí", r)
        r = self.say(bot, ch, "sí")  # no confirma el pedido anterior
        self.assertNotIn("Guardado", r)
        self.assertEqual(rows(), 0)

    def test_am_pm_ya_pasada_pregunta_por_manana(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "recuérdame a las 3 am revisar caja")
        self.assertIn("Las 3:00 a. m. de hoy ya pasaron. ¿Es para mañana?", r)
        self.assertIn("mañana martes 13, 3:00 a. m. (03:00, hora de Perú)", r)
        self.assertIn("¿Lo guardo para mañana?", r)
        r = self.say(bot, ch, "sí")
        self.assertIn("el 2026-10-13 a las 3:00 a. m.", r)
        self.assertEqual(rows(), 1)


if __name__ == "__main__":
    unittest.main()
