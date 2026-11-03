"""Iniciativa propia (Fase 2D) con reloj simulado, sin red: la IA es falsa.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/i -v "$PWD/tests:/app/tests:ro" nexus-core \
        python -m unittest tests.test_iniciativa
"""

import asyncio
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from core import charla, clock, conversa, db, iniciativa, persona, reminders

CANAL, CHAT = "simulado", "1001"
LUNES = date(2026, 11, 2)


class IAFalsa:
    def __init__(self, textos=None, error=None):
        self.textos = list(textos or [])
        self.error = error
        self.llamadas = 0
        self.ultimo_sistema = ""

    async def chat_ex(self, messages, **kw):
        self.llamadas += 1
        self.ultimo_sistema = messages[0]["content"]
        if self.error:
            raise self.error
        return (self.textos.pop(0) if self.textos else "Que tenga una mañana bonita."), {}


class Base(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as c:
            for t in ("reminders", "outbox", "pending_confirmations", "charla", "kv", "iniciativa"):
                c.execute(f"DELETE FROM {t}")
        persona.get.cache_clear()
        persona.reiniciar()
        self.en(0, 6, 0)
        p = mock.patch.object(clock, "now_utc", lambda: self.ahora)
        p.start()
        self.addCleanup(p.stop)
        no_ia = mock.patch("core.conversa.get_client", side_effect=AssertionError("la conversación no debe usar IA"))
        no_ia.start()
        self.addCleanup(no_ia.stop)
        self.ia = IAFalsa()
        p = mock.patch("core.iniciativa.get_client", lambda: self.ia)
        p.start()
        self.addCleanup(p.stop)
        iniciativa.al_escribir(CANAL, CHAT)  # ya le escribió alguna vez: sabe a dónde

    def en(self, dia: int, h: int, m: int = 0) -> None:
        self.ahora = clock.local_to_utc(datetime.combine(LUNES + timedelta(days=dia), datetime.min.time()).replace(hour=h, minute=m))

    def paso(self) -> dict:
        return asyncio.run(iniciativa.due_step()).get("saludos", {})

    def saludos(self) -> list[dict]:
        with db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbox WHERE kind = 'saludo' ORDER BY id")]

    def fila(self, dia: int, tipo: str) -> dict | None:
        return iniciativa._fila(LUNES + timedelta(days=dia), tipo)

    def decir(self, texto: str) -> str:
        return asyncio.run(conversa.conversar([texto], CANAL, CHAT))["texto"]

    def dia_sin_responder(self, dia: int) -> None:
        self.en(dia, 7, 30)
        self.paso()
        self.en(dia, 21, 30)
        self.paso()


class HorariosTest(Base):
    def test_manana_y_noche_a_su_hora_sin_duplicados(self):
        self.en(0, 7, 29)
        self.assertEqual(self.paso(), {})
        self.en(0, 7, 30)
        self.assertEqual(self.paso(), {"manana": "ia"})
        self.en(0, 7, 31)
        self.assertEqual(self.paso(), {}, "no se repite")
        self.en(0, 21, 29)
        self.assertEqual(self.paso(), {})
        self.en(0, 21, 30)
        self.ia.textos = ["¿Cómo le fue hoy en la tienda?"]
        self.assertEqual(self.paso(), {"noche": "ia"})
        s = self.saludos()
        self.assertEqual(len(s), 2)
        self.assertEqual((s[0]["channel"], s[0]["chat_id"]), (CANAL, CHAT))
        historial = [m for m in charla.recientes(10, CHAT) if m["rol"] == "isabelle"]
        self.assertEqual(len(historial), 2, "los saludos entran a la charla")
        self.assertEqual(self.ia.llamadas, 2, "una sola llamada por mensaje")

    def test_silencio_salvo_hora_pedida(self):
        c = dict(iniciativa.config(), manana="05:00")
        with mock.patch.object(iniciativa, "config", lambda: c):
            self.en(0, 5, 0)
            self.assertEqual(self.paso(), {}, "a las 5 es silencio")
            self.en(0, 6, 0)
            self.assertEqual(self.paso(), {"manana": "ia"})
        self.assertTrue(iniciativa._en_silencio(datetime(2026, 11, 2, 23, 0)))
        self.assertTrue(iniciativa._en_silencio(datetime(2026, 11, 2, 5, 59)))
        self.assertFalse(iniciativa._en_silencio(datetime(2026, 11, 2, 6, 0)))

    def test_sistema_apagado_limites_y_reinicio(self):
        self.en(0, 10, 59)
        self.assertEqual(self.paso(), {"manana": "ia"}, "antes de las 11 todavía sale")
        self.en(1, 11, 0)
        self.assertEqual(self.paso(), {})
        self.assertEqual(self.fila(1, "manana")["motivo"], "tarde")
        self.en(0, 23, 0)
        self.assertEqual(self.paso(), {})
        self.assertEqual(self.fila(0, "noche")["motivo"], "tarde")
        # Un apagón a medio redactar: la fila «redactando» reciente no se toca; vieja, se retoma una vez
        self.en(2, 7, 30)
        with db.connect() as c:
            c.execute("INSERT INTO iniciativa (dia, tipo, estado, creado_at, actualizado_at) VALUES"
                      " ('2026-11-04', 'manana', 'redactando', ?, ?)", (self.ahora.isoformat(), self.ahora.isoformat()))
        self.assertEqual(self.paso(), {})
        self.en(2, 7, 40)
        self.assertIn("manana", self.paso())
        self.en(2, 7, 50)
        self.assertEqual(self.paso(), {})
        self.assertEqual(len([s for s in self.saludos() if s["ref_id"] == "2026-11-04:manana"]), 1)


class NoHostigarTest(Base):
    def test_conversacion_activa_espera_y_luego_salta(self):
        self.en(0, 7, 10)
        charla.agregar("jefe", "estoy revisando las cuentas", CHAT)
        self.en(0, 7, 30)
        self.assertEqual(self.paso(), {}, "escribió hace 20 min: espera")
        self.en(0, 7, 40)
        self.assertEqual(self.paso(), {"manana": "ia"}, "ya pasaron 30 min")
        for m in range(0, 80, 10):  # de noche sigue conversando
            self.en(0, 21, 20 + m) if 20 + m < 60 else self.en(0, 22, 20 + m - 60)
            charla.agregar("jefe", "otra cosa", CHAT)
            self.paso()
        self.assertEqual(self.fila(0, "noche")["motivo"], "conversando")

    def test_dias_sin_respuesta_solo_manana_y_pregunta_a_los_3(self):
        self.dia_sin_responder(0)
        self.assertEqual(self.fila(0, "noche")["estado"], "enviado")
        self.dia_sin_responder(1)
        self.assertEqual(self.fila(1, "manana")["estado"], "enviado")
        self.assertEqual(self.fila(1, "noche")["motivo"], "sin_respuesta")
        self.dia_sin_responder(2)
        self.en(3, 7, 30)
        self.assertEqual(self.paso(), {"manana": "pregunta"})
        self.assertIn("¿prefiere que le escriba menos?", self.saludos()[-1]["text"])
        self.assertEqual(self.fila(3, "manana")["dias_sin_respuesta"], 3)
        self.dia_sin_responder(4)
        self.assertEqual(self.fila(4, "manana")["motivo"], "sin_respuesta", "ya preguntó: se calla")
        self.en(4, 22, 0)
        r = self.decir("sí, menos")
        self.assertIn("solo le escribo en las mañanas", r)
        self.assertEqual(iniciativa.preferencia(), "menos")
        self.dia_sin_responder(5)
        self.assertEqual(self.fila(5, "manana")["estado"], "enviado")
        self.assertEqual(self.fila(5, "noche")["motivo"], "preferencia")

    def test_respuestas_a_la_pregunta(self):
        for texto, pref in (("no, como siempre", "normal"), ("pausa", "pausa"), ("mejor menos", "menos"), ("Sí", "menos")):
            with self.subTest(texto=texto):
                with db.connect() as c:
                    iniciativa._set(c, iniciativa.K_PREGUNTA, {"expira": (self.ahora + timedelta(days=1)).isoformat()})
                    iniciativa._set(c, iniciativa.K_PREF, "normal")
                self.decir(texto)
                self.assertEqual(iniciativa.preferencia(), pref)
        self.en(1, 7, 30)
        with db.connect() as c:
            iniciativa._set(c, iniciativa.K_PREF, "pausa")
        self.assertEqual(self.paso(), {})
        self.assertEqual(self.fila(1, "manana")["motivo"], "pausa")
        self.assertIn("como siempre", self.decir("vuelve a saludarme"))
        self.assertEqual(iniciativa.preferencia(), "normal")

    def test_pregunta_sin_responder_no_se_traga_el_mensaje(self):
        with db.connect() as c:
            iniciativa._set(c, iniciativa.K_PREGUNTA, {"expira": (self.ahora + timedelta(days=1)).isoformat()})
        r = self.decir("recuérdame mañana a las 9 am llamar a Piter")
        self.assertIn("llamar a Piter", r)
        self.assertEqual(iniciativa.preferencia(), "normal")
        self.assertFalse(iniciativa.estado()["pregunta_pendiente"], "la pregunta se descarta")

    def test_cualquier_mensaje_reinicia_la_cuenta(self):
        self.dia_sin_responder(0)
        self.dia_sin_responder(1)
        self.en(2, 7, 0)
        self.decir("gracias")
        self.assertEqual(iniciativa.dias_sin_respuesta(LUNES + timedelta(days=2)), 0)
        self.dia_sin_responder(2)
        self.assertEqual(self.fila(2, "noche")["estado"], "enviado")

    def test_despedida_previa_y_ok_ok(self):
        self.en(0, 20, 0)
        self.decir("me voy a dormir, hasta mañana")
        self.en(0, 21, 30)
        self.assertEqual(self.paso(), {})
        self.assertEqual(self.fila(0, "noche")["motivo"], "despedida")
        self.en(1, 1, 0)  # de madrugada, «buenas noches» no salta la noche de ese día
        self.decir("buenas noches")
        self.assertIsNone(self.fila(1, "noche"))
        self.en(1, 7, 30)
        self.paso()
        self.en(1, 8, 0)
        r = self.decir("ok ok")
        self.assertNotIn("?", r)


class RedaccionTest(Base):
    def test_falla_de_ia_usa_respaldo(self):
        self.ia.error = RuntimeError("caída")
        self.en(0, 7, 30)
        self.assertEqual(self.paso(), {"manana": "respaldo"})
        texto = self.saludos()[-1]["text"]
        base = {iniciativa.arranque(o.format(trato="jefe")) for o in iniciativa.config()["respaldo_manana"]}
        self.assertIn(iniciativa.arranque(texto), base)
        self.assertEqual(self.fila(0, "manana")["detalle"], "IA: RuntimeError")

    def test_verificacion_falla_por_cifra_nombre_o_vida_propia(self):
        for salida, motivo in (("Buenos días. Hoy a las 3 p. m. viene su cliente.", "hora 3"),
                               ("Buenos días. Le esperan 48 pedidos.", "cifra 48"),
                               ("Buenos días, ¿ya habló con Marcos sobre el pedido?", "nombre Marcos"),
                               ("Buen día, yo ya desayuné y estoy lista.", "vida propia")):
            with self.subTest(salida=salida):
                with db.connect() as c:
                    c.execute("DELETE FROM iniciativa")
                self.ia.textos = [salida]
                self.en(0, 7, 30)
                self.assertEqual(self.paso(), {"manana": "respaldo"})
                self.assertEqual(self.fila(0, "manana")["detalle"], motivo)

    def test_verificar(self):
        ctx = "Rosa Quispe debe 350 soles. Mañana a las 9:00 a. m.: llamar a Tralvex."
        self.assertIsNone(iniciativa.verificar("Buen día. Hoy toca hablar con Rosa por los 350 soles.", "manana", ctx, []))
        self.assertIsNone(iniciativa.verificar("¿Cómo le fue? Mañana a las 9:00 a. m. llama a Tralvex.", "noche", ctx, []))
        self.assertEqual(iniciativa.verificar("Que descanse, jefe.", "noche", ctx, []), "preguntas")
        self.assertEqual(iniciativa.verificar("Hola. Uno. Dos. Tres. Cuatro.", "manana", ctx, []), "largo o con lista")
        self.assertEqual(iniciativa.verificar("Tiene cinco pendientes.", "manana", ctx, []), "cifra cinco")
        self.assertIn("arranque", iniciativa.verificar("Buenos días, señor. Ánimo.", "manana", ctx, ["Buenos días, jefe."]))

    def test_rechaza_suposiciones_sobre_el_jefe(self):
        ctx = "[RESUMEN DE LA CHARLA]\nContó que está cansado por la semana. Mañana tiene inventario."
        for t in ("Buen día. Se ve que arrancó temprano hoy.", "¿Cómo le fue? Seguro está cansado de tanto trabajo.",
                  "Hola. Debe estar feliz con las ventas.", "Buen día. Imagino que ya desayunó.",
                  "Buenas. Parece que tuvo un día tranquilo, ¿no?", "Buen día. Se nota que la semana pesó.", "Se me hace que hoy va a ser un día movido."):
            with self.subTest(t=t):
                self.assertTrue((iniciativa.verificar(t, "noche" if "?" in t else "manana", ctx, []) or "").startswith("supone"), t)
        # Si lo que sigue sí está en el contexto, no es suposición; preguntar siempre vale
        self.assertIsNone(iniciativa.verificar("Buenas noches. ¿Sigue cansado por la semana?", "noche", ctx, []))
        self.assertIsNone(iniciativa.verificar("Buen día. Seguro que mañana tiene inventario.", "manana", ctx, []))

    def test_el_prompt_pide_preguntar_en_vez_de_suponer(self):
        self.en(0, 7, 30)
        self.paso()
        self.assertIn("pregúntaselo", self.ia.ultimo_sistema)
        self.assertIn("«se ve que»", self.ia.ultimo_sistema)

    def test_no_repite_el_arranque_de_los_ultimos_7(self):
        self.ia.textos = ["Buenos días, jefe. Que le vaya bonito."] * 8
        fuentes = []
        for d in range(8):
            self.en(d, 7, 30)
            fuentes += self.paso().values()
            self.en(d, 8, 0)
            self.decir("gracias")
        self.assertEqual(fuentes[0], "ia")
        self.assertTrue(all(f == "respaldo" for f in fuentes[1:]), fuentes)
        arr = [iniciativa.arranque(s["text"]) for s in self.saludos()]
        for i in range(len(arr)):
            self.assertNotIn(arr[i], arr[max(0, i - 7):i], arr)

    def test_contexto_agenda_ordenada_y_sin_confirmar_una_vez(self):
        base = clock.local_to_utc(datetime(2026, 11, 2))
        reminders.create("reunión con Ana", (base + timedelta(hours=16)).isoformat(), CANAL, CHAT)
        reminders.create("llamar a Piter", (base + timedelta(hours=9)).isoformat(), CANAL, CHAT)
        viejo = reminders.create("pagar la luz", (base - timedelta(days=1, hours=-10)).isoformat(), CANAL, CHAT)
        with db.connect() as c:
            c.execute("UPDATE reminders SET estado = 'sin_confirmar', proximo_envio = NULL WHERE id = ?", (viejo["id"],))
        self.en(0, 7, 30)
        self.paso()
        texto = self.saludos()[-1]["text"]
        self.assertIn("Para hoy tiene:\n• 9:00 a. m.: llamar a Piter\n• 4:00 p. m.: reunión con Ana", texto)
        self.assertIn("sin confirmar", texto)
        self.assertIn("«pagar la luz» (domingo 1)", texto)
        self.assertIn("[AGENDA DE HOY]", self.ia.ultimo_sistema)
        self.assertIn("4:00 p. m.", self.ia.ultimo_sistema)
        self.en(0, 21, 30)
        self.ia.textos = ["¿Cómo le fue con la reunión?"]
        self.paso()
        self.assertNotIn("pagar la luz", self.saludos()[-1]["text"], "se menciona una sola vez")

    def test_noche_sin_agenda_solo_saludo_y_su_dia(self):
        reminders.create("llamar a Piter", clock.local_to_utc(datetime(2026, 11, 3, 9)).isoformat(), CANAL, CHAT)
        self.en(0, 21, 30)
        self.ia.textos = ["¿Cómo le fue hoy? Mañana temprano le toca llamar a Piter."]
        self.assertEqual(self.paso(), {"noche": "ia"})
        self.assertIn("[SU DÍA", self.ia.ultimo_sistema)
        self.assertIn("[AGENDA DE MAÑANA]\n1 recordatorio(s):\n• 9:00 a. m.: llamar a Piter", self.ia.ultimo_sistema)
        self.assertNotIn("Para hoy tiene", self.saludos()[-1]["text"])


class AjustesTest(Base):
    def test_manana_me_levanto_a_las_5_solo_ese_dia(self):
        self.en(0, 20, 0)
        r = self.decir("mañana me levanto a las 5")
        self.assertIn("5:00 a. m.", r)
        martes = LUNES + timedelta(days=1)
        self.assertEqual(iniciativa.hora(martes, "manana"), (datetime(2026, 11, 3, 5, 0), True))
        self.assertEqual(iniciativa.hora(martes + timedelta(days=1), "manana")[0].strftime("%H:%M"), "07:30")
        self.en(1, 5, 0)
        self.assertEqual(self.paso(), {"manana": "ia"}, "la pidió él: sale aunque sea silencio")

    def test_desde_hoy_el_saludo_a_las_6_persistente(self):
        r = self.decir("desde hoy el saludo a las 6")
        self.assertIn("6:00 a. m.", r)
        for d in (1, 5):
            self.assertEqual(iniciativa.hora(LUNES + timedelta(days=d), "manana")[0].strftime("%H:%M"), "06:00")
        self.decir("desde hoy el saludo de la noche a las 10")
        self.assertEqual(iniciativa.hora(LUNES, "noche")[0].strftime("%H:%M"), "22:00")
        self.assertIsNone(iniciativa.ajuste("recuérdame mañana a las 5 pm llamar"))
        self.assertIsNone(iniciativa.ajuste("mañana me levanto temprano"))


if __name__ == "__main__":
    unittest.main()
