"""Recordatorios en lenguaje natural de punta a punta sin red: canal real + API real del núcleo
sobre una base temporal (httpx.ASGITransport, sin el worker). Sin modelo: las fechas se
entienden por reglas.

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
from channels.client import NexusClient
from channels.simulado import OWNER, SimChannel
from core import conversa, db, reminders
from core.main import app

PEDIDO = "recuérdame mañana a las 9 am llamar a Pedro"


def rows(estado: str | None = None) -> int:
    with sqlite3.connect(db.DB_PATH) as c:
        if estado:
            return c.execute("SELECT count(*) FROM reminders WHERE estado = ?", (estado,)).fetchone()[0]
        return c.execute("SELECT count(*) FROM reminders").fetchone()[0]


class ConfirmacionTest(unittest.TestCase):
    def test_classify(self):
        cases = {
            "sí": "si", "si": "si", "Sí, cancélalo": "si", "ok": "si", "dale!": "si", "confirmo": "si",
            "si por favor": "si", "no": "no", "No, déjalo": "no", "mejor no": "no",
            "sí, ¿y a qué hora?": "duda", "si y a que hora": "duda", "quizás": "duda", "": "duda",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(conversa.confirmacion(text), want)


class FlowTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            c.execute("DELETE FROM reminders")
            c.execute("DELETE FROM pending_confirmations")
            c.execute("DELETE FROM kv WHERE key LIKE 'espera:%'")

    def bot(self, chat: str = OWNER) -> tuple[Assistant, SimChannel]:
        """Un bot nuevo = un reinicio: no hay nada en memoria."""
        ch = SimChannel([chat])
        nexus = NexusClient("http://nexus", transport=httpx.ASGITransport(app=app))
        return Assistant(ch, nexus, {chat}, pausa=False), ch

    def say(self, bot: Assistant, ch: SimChannel, text: str, user: str = OWNER) -> str:
        return "\n".join(asyncio.run(bot.handle(ch.text(text, user))))

    def press(self, bot: Assistant, ch: SimChannel, label: str) -> str:
        return "\n".join(asyncio.run(bot.handle(ch.button(label))))

    def test_hora_clara_guarda_sin_preguntar_con_botones(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, PEDIDO)
        self.assertIn("a las 9:00 a. m.", r)
        self.assertIn("llamar a Pedro.", r)
        self.assertEqual([b["texto"] for b in ch.buttons[0]], ["Cambiar", "Cancelar"])
        self.assertEqual(rows(), 1)
        r = self.say(bot, ch, "¿qué recordatorios tengo?")
        self.assertIn("llamar a Pedro", r)
        self.assertIn("n.º", r)

    def test_boton_cancelar(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        clic = ch.button("Cancelar")
        r = "\n".join(asyncio.run(bot.handle(clic)))
        self.assertIn("llamar a Pedro", r)
        self.assertEqual(rows("cancelado"), 1)
        r = "\n".join(asyncio.run(bot.handle(clic)))  # un doble clic no hace nada
        self.assertIn("ya no está activo", r)

    def test_boton_cambiar_y_reinicio(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        r = self.press(bot, ch, "Cambiar")
        self.assertTrue(r.startswith("¿") and r.endswith("?"), r)
        bot, ch = self.bot()  # reinicio entre la pregunta y la nueva hora
        r = self.say(bot, ch, "mañana a las 11 am")
        self.assertIn("11:00 a. m.", r)
        self.assertIn("llamar a Pedro", r)
        self.assertEqual(rows(), 1)
        self.assertTrue(reminders.list_open(OWNER)[0]["due_local"].endswith("11:00"))

    def test_cambiar_solo_la_hora_conserva_el_dia(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)  # mañana a las 9
        dia = reminders.list_open(OWNER)[0]["due_local"][:10]
        self.press(bot, ch, "Cambiar")
        r = self.say(bot, ch, "mejor a las 10 am")
        self.assertIn("10:00 a. m.", r)
        self.assertEqual(reminders.list_open(OWNER)[0]["due_local"], f"{dia} 10:00")

    def test_borrar_pide_confirmacion_y_sobrevive_reinicio(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        rid = reminders.list_open(OWNER)[0]["id"]
        r = self.say(bot, ch, f"cancela el recordatorio {rid}")
        self.assertIn("¿Cancelo el recordatorio", r)
        self.assertEqual(rows("creado"), 1)
        bot, ch = self.bot()  # reinicio
        r = self.say(bot, ch, "sí")
        self.assertIn("llamar a Pedro", r)
        self.assertEqual(rows("cancelado"), 1)

    def test_borrar_no_lo_deja(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        rid = reminders.list_open(OWNER)[0]["id"]
        self.say(bot, ch, f"borra el {rid}")
        r = self.say(bot, ch, "no")
        self.assertIn("Lo dejo como está", r)
        self.assertEqual(rows("creado"), 1)

    def test_otro_chat_no_cancela(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        rid = reminders.list_open(OWNER)[0]["id"]
        bot2, ch2 = self.bot("2002")
        r = self.say(bot2, ch2, f"cancela el recordatorio {rid}", user="2002")
        self.assertIn("No encuentro", r)
        self.assertEqual(rows("creado"), 1)

    def test_ya_lo_hice(self):
        bot, ch = self.bot()
        self.say(bot, ch, PEDIDO)
        with db.connect() as c:  # el aviso ya salió
            c.execute("UPDATE reminders SET estado = 'avisado', last_sent_at = ?", (datetime.now().isoformat(),))
        r = self.say(bot, ch, "ya lo hice")
        self.assertIn("llamar a Pedro", r)
        self.assertEqual(rows("hecho"), 1)

    def test_sin_fecha_pregunta(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "recuérdame algo sin fecha")
        self.assertIn("No entendí la fecha", r)
        self.assertEqual(rows(), 0)


NOW_415 = datetime(2026, 10, 12, 4, 15)  # el fallo real: 04:15 en Perú


class HoraDudosaTest(FlowTest):
    """Con la hora simulada (04:15): solo se pregunta cuando la hora es dudosa."""

    def setUp(self):
        super().setUp()
        p = mock.patch("core.clock.now_local", lambda: NOW_415)
        p.start()
        self.addCleanup(p.stop)

    def test_formato_hora_y_tarea(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "Recuerdame hoy a las 4 : 17 llamar a Piter")
        self.assertIn("hoy a las 4:17 a. m.: llamar a Piter.", r)
        self.assertEqual(rows(), 1)

    def test_hora_ambigua_pregunta_y_aclara(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "recuérdame hoy a las 4 17 llamar a Piter")
        self.assertIn("¿A qué hora exactamente? Ejemplo: 4:17 pm o 16:17", r)
        self.assertEqual(rows(), 0)
        bot, ch = self.bot()  # la aclaración sobrevive al reinicio
        r = self.say(bot, ch, "4:17 pm")
        self.assertIn("hoy a las 4:17 p. m.: llamar a Piter.", r)
        self.assertEqual(rows(), 1)

    def test_ya_pasada_pregunta_por_manana(self):
        bot, ch = self.bot()
        r = self.say(bot, ch, "recuérdame a las 3 am revisar caja")
        self.assertIn("Las 3:00 a. m. de hoy ya pasaron. ¿Es para mañana?", r)
        self.assertIn("¿Se lo guardo para mañana?", r)
        self.assertEqual(rows(), 0)
        r = self.say(bot, ch, "sí")
        self.assertIn("mañana martes 13 a las 3:00 a. m.: revisar caja.", r)
        self.assertEqual(rows(), 1)

    def test_parallel_yes_creates_one(self):
        bot, ch = self.bot()
        self.say(bot, ch, "recuérdame a las 3 am revisar caja")

        async def both():
            return await asyncio.gather(bot.handle(ch.text("sí")), bot.handle(ch.text("ok")))

        asyncio.run(both())
        self.assertEqual(rows(), 1)


if __name__ == "__main__":
    unittest.main()
