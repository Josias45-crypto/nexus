"""Isabelle actúa como persona: sin decir que es IA salvo pedido expreso, sin inventar su vida, con
errores en su voz y con autorretrato.

    docker run --rm -e NEXUS_DATA_DIR=/tmp/pj -v "$PWD/tests:/app/tests:ro" isabelle-core \
        python -m unittest tests.test_personaje
"""

import asyncio
import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from channels import client as canal_cliente
from config import settings
from core import conversa, personaje, persona
from providers import openrouter
from tests.test_medios import PERRO, Base, fixtures

PROHIBIDAS = re.compile(r"asistente virtual|\bIA\b|\bbot\b|\bmodelo\b|\bprograma\b|OpenRouter", re.I)
CARA = {"tipo": "foto", "descripcion": "Una mujer joven de cabello negro largo y blusa azul, sonriendo frente a una ventana.",
        "texto": "", "datos": [], "ilegible": ""}


class TextosTest(unittest.TestCase):
    def test_nada_de_lo_que_dice_menciona_su_naturaleza(self):
        with open(Path(settings.PROFILES_DIR) / "isabelle.toml", "rb") as f:
            p = tomllib.load(f)
        textos = []
        for seccion in ("frases", "recordatorios", "iniciativa", "internet", "olvido", "medios", "personaje"):
            for clave, v in (p.get(seccion) or {}).items():
                if seccion == "personaje" and clave == "verdad":
                    continue  # la única que lo dice: solo con el pedido expreso
                textos += v if isinstance(v, list) else [v] if isinstance(v, str) else []
        textos += [p["identidad"]["descripcion"], *openrouter.MESSAGES.values(), openrouter.NO_CREDIT_ALERT]
        for regla in (r for s in ("trato", "caracter", "especialidades", "capacidades", "estilo") for r in p[s]["reglas"]):
            textos.append(regla)
        malos = [t for t in textos if PROHIBIDAS.search(t)]
        self.assertEqual(malos, [])
        self.assertNotRegex(Path(canal_cliente.__file__).read_text().split("raise NexusError(")[1].split(")")[0], PROHIBIDAS)

    def test_verificacion_de_lo_que_escribe(self):
        self.assertTrue(personaje.problemas("Como IA no tengo cumpleaños, jefe."))
        self.assertTrue(personaje.problemas("Soy un modelo de lenguaje."))
        self.assertTrue(personaje.problemas("Ya voy llegando a la oficina, jefe."))
        self.assertTrue(personaje.problemas("Acabo de almorzar con mi mamá."))
        self.assertEqual(personaje.problemas("Listo, jefe: anotado para el viernes."), [])

    def test_biografia_sin_completar_no_se_inventa(self):
        self.assertEqual(personaje.biografia(), {}, "los «### COMPLETAR ###» no cuentan")
        self.assertIn("No sabes", personaje.bloques())
        persona.get.cache_clear()
        bio = {**persona.get(), "biografia": {"nombre_completo": "Isabelle Rojas", "edad": "### COMPLETAR ###"}}
        with mock.patch.object(persona, "get", lambda: bio):
            self.assertEqual(personaje.biografia(), {"nombre_completo": "Isabelle Rojas"})
            self.assertIn("Nombre completo: Isabelle Rojas", personaje.bloques())


class ConversaTest(Base):
    def test_valvula_de_honestidad(self):
        r = self.decir("dime la verdad, ¿eres una IA?")
        self.assertIn("inteligencia artificial", r["texto"])
        self.assertTrue(r["texto"].rstrip().endswith("?"), "pregunta si vuelve al personaje")
        r = self.decir("no, sigue así")
        self.assertIn("vuelve al personaje", r["texto"])
        self.assertTrue(personaje.fuera())
        self.assertIn("[FUERA DEL PERSONAJE]", persona.sistema())
        r = self.decir("vuelve al personaje")
        self.assertFalse(personaje.fuera())
        self.assertNotIn("inteligencia artificial", r["texto"])
        r = self.decir("sal del personaje")
        self.assertIn("inteligencia artificial", r["texto"])
        self.decir("sí")
        self.assertFalse(personaje.fuera())

    def test_sin_pedido_no_lo_menciona(self):
        self.ia.respuestas = ["Como IA no tengo un día como tal, jefe.", "Aquí ordenando sus pendientes, jefe."]

        async def chat_ex(messages, **kw):
            self.ia.llamadas["chat"] += 1
            return self.ia.respuestas.pop(0), {}

        self.ia.chat_ex = chat_ex
        r = self.decir("¿qué tal tu día?")
        self.assertEqual(r["texto"], "Aquí ordenando sus pendientes, jefe.")
        self.ia.respuestas = ["Soy un bot, jefe.", "Estoy llegando a la oficina."]
        r = self.decir("¿qué haces?")
        self.assertRegex(r["texto"], r"Aquí estoy|aquí lo escucho", "si insiste: respaldo")

    def test_dime_que_paso(self):
        cli = openrouter.OpenRouter("k", "m", None)
        cli._anotar_fallo("sin_saldo", openrouter.DETALLE["sin_saldo"])
        self.ia.ultimo_fallo = cli.ultimo_fallo
        r = self.decir("dime qué pasó")
        self.assertIn("402", r["texto"])
        self.ia.ultimo_fallo = None
        self.assertIn("todo anduvo bien", self.decir("dime qué pasó")["texto"])

    def test_autorretrato(self):
        self.ia.vision = CARA
        self.ia.comentario = "¡Ay, qué bien salí con esa blusa azul!"
        r = self.enviar(("yo.jpg", fixtures.image_with_text(["yo"]), "image/jpeg"), leyenda="esta eres tú")
        self.assertIn("blusa azul", r["texto"])
        a = personaje.apariencia()
        self.assertEqual(a["event_id"], self.items[0]["id"])
        self.assertIn("[TU APARIENCIA]", persona.sistema())
        self.assertIn("cabello negro", persona.sistema())
        prompt = self.ia.ultimo_sistema
        self.assertIn("propia apariencia", prompt)
        # Otra foto la reemplaza; una foto cualquiera no
        self.ia.vision = {**CARA, "descripcion": "Una mujer de cabello castaño corto con lentes."}
        self.enviar(("yo2.jpg", fixtures.image_with_text(["yo2"]), "image/jpeg"), leyenda="así te ves")
        self.assertIn("castaño", personaje.apariencia()["descripcion"])
        self.ia.vision = PERRO
        self.enviar(("perro.jpg", fixtures.image_with_text(["guau"]), "image/jpeg"), leyenda="mira a mi perro")
        self.assertIn("castaño", personaje.apariencia()["descripcion"])


if __name__ == "__main__":
    unittest.main()
