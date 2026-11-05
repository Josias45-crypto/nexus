"""El jefe simulado: escribe como un dueño de negocio peruano, según su perfil.

Cada mensaje del guion se reescribe con el estilo del perfil: primero transformaciones fijas
(faltas, abreviaturas, sin signos, formal, emojis) y, en parte de los turnos, el modelo barato
(OpenRouter, el mismo modelo diario). Los datos clave (nombres, cifras, horas, días) se conservan
siempre: si la reescritura de la IA los pierde, queda la versión fija (así un fallo no es del
simulador).
"""

import random
import re
import unicodedata

PERFILES = ("normal", "apurado", "voz", "formal", "vago", "emojis", "largo")
ABREVIATURAS = [(r"\bque\b", "q"), (r"\bporque\b", "xq"), (r"\bpor favor\b", "x fa"), (r"\bpor\b", "x"), (r"\btambién\b", "tmb"),
                (r"\bmañana\b", "mñn"), (r"\bmensaje\b", "msj"), (r"\bmucho\b", "mxo"), (r"\bdónde\b", "dnd"), (r"\bcuándo\b", "cuando")]
RELLENO = ("Mira, te cuento que hoy fue un día movido en la tienda, llegaron varios clientes y entre una cosa y otra "
           "se me pasó la mañana volando, pero bueno, lo importante es esto: ")
EMOJIS = ["😅", "👍", "🙏", "😂", "🤔"]


def sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def fijo(texto: str, perfil: str, rnd: random.Random) -> str:
    t = texto
    if perfil == "apurado":
        t = sin_tildes(t.lower())
        for a, b in ABREVIATURAS:
            if rnd.random() < 0.8:
                t = re.sub(a, b, t)
        t = t.replace("¿", "").replace("¡", "")
        if rnd.random() < 0.4:
            t = re.sub(r"([aeiou])([a-z])", lambda m: m.group(2) + m.group(1) if rnd.random() < 0.08 else m.group(0), t)
    elif perfil == "voz":  # nota de voz transcrita: sin signos ni mayúsculas
        t = re.sub(r"[¿?¡!.,;:]", "", t.lower())
        t = ("eh " if rnd.random() < 0.5 else "") + t
    elif perfil == "formal":
        t = f"Buenas, Isabelle. {t[:1].upper() + t[1:]}" + (" Muchas gracias." if rnd.random() < 0.5 else "")
    elif perfil == "emojis":
        t = f"{t} {rnd.choice(EMOJIS)}"
    elif perfil == "largo":
        t = RELLENO + t
    return t


class Jefe:
    def __init__(self, perfil: str, semilla: int, ia=None, prob_ia: float = 0.35):
        self.perfil = perfil
        self.rnd = random.Random(semilla)
        self.ia = ia  # async (prompt) -> str, o None
        self.prob_ia = prob_ia
        self.costo = 0.0

    async def __call__(self, texto: str, conservar: list[str] | tuple = (), libre: bool = True) -> str:
        """libre=False: sin reescritura de la IA (cuando hasta un detalle cambia el caso, p. ej. una hora ambigua)."""
        base = fijo(texto, self.perfil, self.rnd)
        if not libre or not self.ia or self.perfil in ("normal", "largo") or self.rnd.random() > self.prob_ia:
            return base
        estilo = {"apurado": "apurado, con faltas, sin tildes y abreviaturas (q, xq, x fa)",
                  "voz": "como una nota de voz transcrita: todo en minúsculas, sin signos de puntuación, con alguna muletilla",
                  "formal": "formal y educado, de usted", "vago": "vago e impreciso, pero sin cambiar los datos",
                  "emojis": "relajado y con un emoji"}.get(self.perfil, "natural")
        prompt = ("Reescribe este mensaje como lo mandaría por WhatsApp un dueño de negocio peruano a su secretaria, con estilo "
                  f"{estilo}. Conserva EXACTAMENTE estos datos sin cambiarlos: {', '.join(conservar) or 'todos los datos'}. "
                  f"No agregues datos nuevos. Responde solo el mensaje.\n\nMensaje: {texto}")
        try:
            nuevo, costo = await self.ia(prompt)
            self.costo += costo
        except Exception:
            return base
        nuevo = (nuevo or "").strip().strip('"«»')
        # «Claro, acá te va el mensaje reescrito: …»: el preámbulo del modelo no es del jefe
        nuevo = re.sub(r"^(claro|aqu[ií]|ac[aá]|listo|por supuesto|ok)\b[^\n:]{0,80}(reescrit|mensaje|versi[oó]n)[^\n:]*:\s*",
                       "", nuevo, flags=re.I).strip().strip('"«»')
        if re.search(r"reescrit|aqu[ií] (tienes|va)|ac[aá] (te va|tienes)", nuevo, re.I):
            return base
        plano = sin_tildes(nuevo.lower())
        if not nuevo or len(nuevo) > 3 * len(texto) + 80 or any(sin_tildes(c.lower()) not in plano for c in conservar):
            return base  # perdió un dato clave: la versión fija
        return nuevo
