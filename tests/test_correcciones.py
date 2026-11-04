"""Regresiones de lo que encontró el simulador (Parte 4).

- «Buenas, Isabelle. Recuérdame…»: el recordatorio se crea aunque el pedido no vaya al inicio (antes
  la charla decía «queda anotado» sin crear nada y el aviso nunca llegaba).
- «no, era el jueves» corrige de verdad el recordatorio recién creado.
- La charla no puede prometer avisos que no creó ni suponer cosas del jefe.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/co -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_correcciones
"""

import unittest

from core import clock, reminders
from tests.test_medios import CHAT, Base


class RecordatorioTest(Base):
    def abiertos(self) -> list[dict]:
        return reminders.list_open(CHAT)

    def test_pedido_despues_de_un_saludo(self):
        for texto in ("Buenas, Isabelle. Recuérdame en 10 minutos mandar la proforma",
                      "Mira, te cuento que hoy fue un día movido, lo importante: recuérdame mañana a las 11 am revisar los pedidos"):
            with self.subTest(texto=texto):
                r = self.decir(texto)
                self.assertTrue(r["botones"], "creado de verdad: trae Cambiar y Cancelar")
        self.assertEqual(len(self.abiertos()), 2)

    def test_no_era_el_jueves(self):
        self.decir("recuérdame el miércoles a las 4 pm llamar al contador")
        r = self.decir("no, era el jueves")
        self.assertIn("Corregido", r["texto"])
        [rec] = self.abiertos()
        local = clock.utc_to_local(rec["due_at"])
        self.assertEqual((local.weekday(), local.hour, local.minute), (3, 16, 0), "jueves, misma hora")

    def test_mejor_a_otra_hora(self):
        self.decir("recuérdame el viernes a las 10 am pagar la luz")
        self.decir("mejor a las 5 pm")
        [rec] = self.abiertos()
        local = clock.utc_to_local(rec["due_at"])
        self.assertEqual((local.weekday(), local.hour), (4, 17), "mismo viernes, 5 p. m.")

    def test_un_no_cualquiera_no_corrige(self):
        self.decir("recuérdame el viernes a las 10 am pagar la luz")
        self.decir("no, gracias")
        [rec] = self.abiertos()
        self.assertEqual(clock.utc_to_local(rec["due_at"]).hour, 10)


class Ronda1Test(Base):
    """Lo que encontró la ronda 1 del simulador."""

    def test_un_hoy_en_el_preambulo_no_vuelve_la_pregunta_por_fecha(self):
        from core import conversa

        self.assertFalse(conversa._temporal(conversa._pregunta_final(
            "Mira, hoy fue un día movido en la tienda, lo importante: ¿cuánto me debe Rosa?")))
        self.assertTrue(conversa._temporal(conversa._pregunta_final("¿qué aprendí hoy?")))

    def test_cortesia_alrededor_del_pedido(self):
        from core import conversa

        self.assertEqual(conversa._sin_cortesia("Buenas, Isabelle. De la noche Muchas gracias."), "De la noche")
        self.assertEqual(conversa._sin_cortesia("eh olvida lo de Zorvel"), "olvida lo de Zorvel")
        self.assertEqual(conversa._sin_cortesia("gracias"), "gracias")

    def test_responde_la_hora_con_saludo_y_gracias(self):
        self.decir("recuérdame mañana a las 7 sacar la mercadería")
        r = self.decir("Buenas, Isabelle. De la noche, muchas gracias.")
        self.assertIn("7:00 p. m.", r["texto"])

    def test_nota_disponible_al_instante(self):
        self.decir("recuerda esto: Tralvex entrega los martes")
        from core.db import connect

        with connect() as c:
            estado = c.execute("SELECT status FROM events WHERE kind = 'text'").fetchone()["status"]
        self.assertEqual(estado, "processed", "si pregunta enseguida, ya lo sabe")

    def test_todos_los_dias_a_las_9_es_un_recordatorio(self):
        r = self.decir("recuérdame todos los días a las 9 am revisar el inventario")
        self.assertNotIn("saludo", r["texto"])
        self.assertEqual(len(reminders.list_open(CHAT)), 1)

    def test_se_cancela_uno_sin_confirmar(self):
        rid = reminders.create("pagar la luz", (clock.now_utc()).isoformat(), "simulado", CHAT)["id"]
        from core.db import connect

        with connect() as c:
            c.execute("UPDATE reminders SET estado = 'sin_confirmar', proximo_envio = NULL WHERE id = ?", (rid,))
        r = self.decir(f"cancela el recordatorio {rid}")
        self.assertNotIn("No encuentro", r["texto"])


class CharlaTest(Base):
    def charla(self, *respuestas: str) -> dict:
        cola = list(respuestas)

        async def chat_ex(messages, **kw):
            self.ia.llamadas["chat"] += 1
            return cola.pop(0), {}

        self.ia.chat_ex = chat_ex
        return self.decir("oye, una cosa del contador")

    def test_no_promete_un_aviso_que_no_creo(self):
        r = self.charla("Queda anotado: en 10 minutos le recuerdo lo del contador.", "Cuénteme, jefe, ¿qué pasa con el contador?")
        self.assertEqual(r["texto"], "Cuénteme, jefe, ¿qué pasa con el contador?")
        self.assertEqual(reminders.list_open(CHAT), [])

    def test_no_finge_haber_olvidado(self):
        r = self.charla("Listo, ya lo olvidé.", "¿Qué quiere que olvide, jefe?")
        self.assertEqual(r["texto"], "¿Qué quiere que olvide, jefe?")

    def test_no_supone_como_esta(self):
        r = self.charla("Se le ve con buena energía hoy, jefe. ¿Qué pasa con el contador?", "¿Qué pasa con el contador, jefe?")
        self.assertEqual(r["texto"], "¿Qué pasa con el contador, jefe?")


if __name__ == "__main__":
    unittest.main()
