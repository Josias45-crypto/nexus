"""Pruebas del intérprete de fechas (solo librería estándar).

Uso: python3 -m unittest tests.test_when
"""

import unittest
from datetime import datetime

from core.when import describe, parse

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


class WhenTest(unittest.TestCase):
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
        self.assertEqual(
            describe(datetime(2026, 10, 9, 9, 0), NOW), "pasado mañana viernes 9 de octubre a las 09:00"
        )


if __name__ == "__main__":
    unittest.main()
