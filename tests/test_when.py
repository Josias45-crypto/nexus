"""Pruebas del intérprete de fechas (solo librería estándar).

Uso: python3 -m unittest tests.test_when
"""

import unittest
from datetime import datetime

from core.when import Ambiguous, ampm, describe, parse

NOW = datetime(2026, 10, 7, 10, 30)  # miércoles 10:30

CASES = [
    ("recuérdame llamar a Pedro el viernes a las 9", "2026-10-09 09:00", "llamar a Pedro"),
    ("Recuérdame mañana a las 9 revisar el correo", "2026-10-08 09:00", "revisar el correo"),
    ("recuerdame que pasado mañana pague la luz", "2026-10-09 09:00", "pague la luz"),
    ("recuérdame en 2 horas sacar el pan", "2026-10-07 12:30", "sacar el pan"),
    ("recuérdame en media hora llamar a mamá", "2026-10-07 11:00", "llamar a mamá"),
    ("recuérdame en tres días a las 10 enviar la cotización", "2026-10-10 10:00", "enviar la cotización"),
    ("recuérdame el 15 de octubre a las 4 de la tarde la reunión con Ana", "2026-10-15 16:00", "la reunión con Ana"),
    ("recuérdame el 20/10 cobrar a Luis", "2026-10-20 09:00", "cobrar a Luis"),
    ("recuérdame a las 3 llamar al banco", "2026-10-07 15:00", "llamar al banco"),
    ("recuérdame a las 8 de la mañana tomar la pastilla", "2026-10-08 08:00", "tomar la pastilla"),
    ("recuérdame hoy a las 18:30 cerrar caja", "2026-10-07 18:30", "cerrar caja"),
    ("recuérdame esta noche apagar el horno", "2026-10-07 20:00", "apagar el horno"),
    ("recuérdame el miércoles revisar inventario", "2026-10-14 09:00", "revisar inventario"),
    ("recuérdame el próximo miércoles a las 11 revisar inventario", "2026-10-14 11:00", "revisar inventario"),
    ("recuérdame al mediodía almorzar", "2026-10-07 12:00", "almorzar"),
    ("recuérdame 7pm gimnasio", "2026-10-07 19:00", "gimnasio"),
    ("recuérdame mañana por la mañana ir al banco", "2026-10-08 09:00", "ir al banco"),
    ("/recordar el 2 de enero renovar el dominio", "2027-01-02 09:00", "renovar el dominio"),
]

# Fallo real en Telegram: lunes 12 de octubre a las 04:15 (hora de Perú)
NOW_415 = datetime(2026, 10, 12, 4, 15)
CASES_415 = [
    # Los dos casos exactos vistos en Telegram
    ("Recuerdame hoy a las 4 : 17 llamar a Piter", "2026-10-12 04:17", "llamar a Piter"),
    ("Recuerdame hoy a las 4 y 18 llamar a Pedro", "2026-10-12 04:18", "llamar a Pedro"),
    # Formatos de hora
    ("recuérdame a las 4:17 llamar a Piter", "2026-10-12 04:17", "llamar a Piter"),
    ("recuérdame hoy a las 4.17 pagar la luz", "2026-10-12 04:17", "pagar la luz"),
    ("recuérdame a las 4h17 pagar la luz", "2026-10-12 04:17", "pagar la luz"),
    ("recuérdame a las 4 y media abrir la tienda", "2026-10-12 04:30", "abrir la tienda"),
    ("recuérdame a las 5 menos cuarto abrir la tienda", "2026-10-12 04:45", "abrir la tienda"),
    ("recuérdame a las 8 y 5 minutos tomar el bus", "2026-10-12 08:05", "tomar el bus"),
    ("recuérdame hoy a las 11 y 45 llamar a Ana", "2026-10-12 11:45", "llamar a Ana"),
    ("recuérdame a las cuatro y media llamar a Ana", "2026-10-12 04:30", "llamar a Ana"),
    ("recuérdame a las 16:17 llamar a Piter", "2026-10-12 16:17", "llamar a Piter"),
    ("recuérdame a las 4:17 pm llamar a Piter", "2026-10-12 16:17", "llamar a Piter"),
    ("recuérdame 7pm gimnasio", "2026-10-12 19:00", "gimnasio"),
    ("recuérdame a las 9 de la noche cerrar caja", "2026-10-12 21:00", "cerrar caja"),
    ("recuérdame a las 3 de la tarde cobrar a Luis", "2026-10-12 15:00", "cobrar a Luis"),
    ("recuérdame a las 6 de la mañana correr", "2026-10-12 06:00", "correr"),
    ("recuérdame al mediodía almorzar", "2026-10-12 12:00", "almorzar"),
    ("recuérdame a medianoche apagar el horno", "2026-10-13 00:00", "apagar el horno"),
    ("recuérdame a las 12 de la noche apagar el horno", "2026-10-13 00:00", "apagar el horno"),
    # Próxima ocurrencia entre h y h+12 (sin am/pm ni fecha)
    ("recuérdame a las 4 llamar a Piter", "2026-10-12 16:00", "llamar a Piter"),
    ("recuérdame a las 4 y cuarto llamar", "2026-10-12 16:15", "llamar"),  # 04:15 no es futuro
    ("recuérdame hoy a las 12 almorzar", "2026-10-12 12:00", "almorzar"),
    # Relativas y con fecha (con fecha no cambia: de 1 a 7 es de la tarde)
    ("recuérdame en 20 minutos sacar el pan", "2026-10-12 04:35", "sacar el pan"),
    ("recuérdame mañana 9 llamar al banco", "2026-10-13 09:00", "llamar al banco"),
    ("recuérdame mañana 16:30 recoger el pedido", "2026-10-13 16:30", "recoger el pedido"),
    ("recuérdame mañana a las 4 visitar a Rosa", "2026-10-13 16:00", "visitar a Rosa"),
    ("recuérdame el viernes a las 3 de la tarde reunión con Ana", "2026-10-16 15:00", "reunión con Ana"),
    ("recuérdame el 15 de octubre a las 10:30 pagar el alquiler", "2026-10-15 10:30", "pagar el alquiler"),
]
AMBIGUOUS = [
    (NOW_415, "recuérdame hoy a las 4 17 llamar a Piter"),  # sobra un número
    (NOW_415, "recuérdame hoy a las 25 llamar"),
    (NOW_415, "recuérdame a las 16 am llamar"),
    (NOW_415, "recuérdame a las 4 y llamar a Pedro"),  # conector suelto tras la hora
    (datetime(2026, 10, 12, 23, 0), "recuérdame hoy a las 3 llamar"),  # 3:00 y 15:00 ya pasaron
    (NOW_415, "recuérdame hoy a las cuatro y pico llamar"),
]


class WhenTest(unittest.TestCase):
    def test_cases_415(self):
        for text, expected, task in CASES_415:
            with self.subTest(text=text):
                p = parse(text, NOW_415)
                self.assertIsNotNone(p)
                self.assertEqual(p.when.strftime("%Y-%m-%d %H:%M"), expected)
                self.assertEqual(p.task, task)
                self.assertFalse(p.passed_today)

    def test_ambiguous_asks(self):
        for now, text in AMBIGUOUS:
            with self.subTest(text=text):
                with self.assertRaises(Ambiguous):
                    parse(text, now)

    def test_explicit_time_already_passed(self):
        """Con am/pm explícito y ya pasada hoy: se propone mañana y se marca para preguntar."""
        for now, text, expected in [
            (NOW_415, "recuérdame a las 3 am revisar caja", "2026-10-13 03:00"),
            (NOW_415, "recuérdame hoy a las 3:30 am revisar caja", "2026-10-13 03:30"),
            (datetime(2026, 10, 12, 15, 0), "recuérdame a las 2 pm revisar caja", "2026-10-13 14:00"),
            (datetime(2026, 10, 12, 15, 0), "recuérdame a las 14:30 revisar caja", "2026-10-13 14:30"),
        ]:
            with self.subTest(text=text):
                p = parse(text, now)
                self.assertEqual(p.when.strftime("%Y-%m-%d %H:%M"), expected)
                self.assertTrue(p.passed_today)
                self.assertEqual(p.task, "revisar caja")

    def test_cases(self):
        for text, expected, task in CASES:
            with self.subTest(text=text):
                p = parse(text, NOW)
                self.assertIsNotNone(p)
                self.assertEqual(p.when.strftime("%Y-%m-%d %H:%M"), expected)
                self.assertEqual(p.task, task)

    def test_sin_fecha(self):
        self.assertIsNone(parse("recuérdame comprar leche", NOW))
        self.assertIsNone(parse("recuérdame el 31 de febrero algo", NOW))

    def test_describe(self):
        self.assertEqual(describe(datetime(2026, 10, 12, 4, 17), NOW_415), "hoy 4:17 a. m.")
        self.assertEqual(describe(datetime(2026, 10, 12, 16, 0), NOW_415), "hoy 4:00 p. m.")
        self.assertEqual(describe(datetime(2026, 10, 13, 9, 0), NOW_415), "mañana martes 13, 9:00 a. m.")
        self.assertEqual(
            describe(datetime(2026, 10, 16, 15, 0), NOW_415), "viernes 16 de octubre, 3:00 p. m."
        )
        self.assertEqual(
            describe(datetime(2026, 10, 9, 9, 0), NOW), "pasado mañana viernes 9, 9:00 a. m."
        )
        self.assertEqual(ampm(datetime(2026, 1, 1, 12, 0)), "12:00 p. m.")
        self.assertEqual(ampm(datetime(2026, 1, 1, 0, 5)), "12:05 a. m.")


if __name__ == "__main__":
    unittest.main()
