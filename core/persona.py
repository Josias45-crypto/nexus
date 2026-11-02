"""Personalidad de Isabelle: profiles/isabelle.toml (editable, sin tocar el código).

sistema() arma el bloque [QUIÉN ERES] del prompt; frase() devuelve una frase fija de la
personalidad con un tratamiento que varía ("jefe", "señor Josias"...).

dosificar() es la última mano a todo lo que Isabelle dice (lo arme el código o la IA), para que
no dependa de que el modelo obedezca:
- Tratamiento (jefe, señor...) en la mitad de los mensajes como mucho (nunca en dos seguidos),
  una sola vez por mensaje y distinto del último usado.
- Emojis: como mucho uno por mensaje y en no más de uno de cada tres mensajes.
"""

import logging
import random
import re
import tomllib
from collections import deque
from functools import lru_cache
from pathlib import Path

from config import settings

log = logging.getLogger("nexus.persona")

ARCHIVO = "isabelle.toml"
SECCIONES = ("identidad", "trato", "caracter", "especialidades", "capacidades", "estilo", "honestidad", "frases")
RECORDATORIOS = ("previo", "hora", "insistencia1", "insistencia2", "insistencia3", "retrasado", "creado",
                 "hecho", "pospuesto", "cancelado", "cuando")
FRASES = ("guardado", "cierre", "gracias", "despedida", "noche", "no_entendi", "foto", "audio", "archivo", "duplicado",
          "sin_memoria", "profundo", "ayuda")


class PersonaError(ValueError):
    pass


def _validar(data: dict) -> dict:
    faltan = [s for s in SECCIONES if not isinstance(data.get(s), dict)]
    if faltan:
        raise PersonaError(f"{ARCHIVO}: faltan las secciones {', '.join(faltan)}")
    if not str(data["identidad"].get("nombre") or "").strip():
        raise PersonaError(f"{ARCHIVO}: falta identidad.nombre")
    trat = data["trato"].get("tratamientos")
    if not isinstance(trat, list) or not trat or not all(isinstance(t, str) and t.strip() for t in trat):
        raise PersonaError(f"{ARCHIVO}: trato.tratamientos debe ser una lista de textos")
    for s in SECCIONES[1:-1]:
        reglas = data[s].get("reglas")
        if not isinstance(reglas, list) or not all(isinstance(r, str) for r in reglas):
            raise PersonaError(f"{ARCHIVO}: {s}.reglas debe ser una lista de textos")
    for seccion, claves in (("frases", FRASES), ("recordatorios", RECORDATORIOS)):
        for f in claves:
            v = (data.get(seccion) or {}).get(f)
            if not (isinstance(v, str) and v.strip()) and not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
                raise PersonaError(f"{ARCHIVO}: falta {seccion}.{f} (texto o lista de textos)")
    return data


@lru_cache
def get() -> dict:
    path = Path(settings.PROFILES_DIR) / ARCHIVO
    if not path.is_file():
        raise PersonaError(f"No existe {path}: es la personalidad de Isabelle")
    with path.open("rb") as f:
        data = _validar(tomllib.load(f))
    log.info("Personalidad cargada: %s", data["identidad"]["nombre"])
    return data


def nombre() -> str:
    return get()["identidad"]["nombre"]


def tratamiento() -> str:
    """Uno al azar, distinto del último que llegó a usarse."""
    todos = get()["trato"]["tratamientos"]
    return random.choice([t for t in todos if t.lower() != _ultimo_trato] or todos)


_ultima: dict[str, str] = {}  # clave -> última variante usada (no se repite seguida)


def frase(clave: str, mayuscula: bool = True, **datos) -> str:
    """Una frase fija de la personalidad ("cierre" o "recordatorios.hora"), con {trato} variando
    y sin repetir la variante anterior de esa clave. Empieza en mayúscula."""
    seccion, _, nombre = clave.rpartition(".")
    valor = get()[seccion or "frases"][nombre]
    opciones = valor if isinstance(valor, list) else [valor]
    candidatas = [o for o in opciones if o != _ultima.get(clave)] or opciones
    elegida = random.choice(candidatas)
    _ultima[clave] = elegida
    texto = elegida.strip().format(trato=tratamiento(), **datos)
    if not mayuscula:
        return texto
    i = next((i for i, c in enumerate(texto) if c.isalpha()), 0)  # «⏰ jefe…» -> «⏰ Jefe…»
    return texto[:i] + texto[i:i + 1].upper() + texto[i + 1:]


def sistema() -> str:
    """Bloque [QUIÉN ERES]: identidad, trato, carácter, especialidades, estilo y honestidad."""
    p = get()
    partes = [" ".join(p["identidad"]["descripcion"].split())]
    for s in SECCIONES[1:-1]:
        partes += [f"- {r}" for r in p[s]["reglas"]]
    return "\n".join(partes)


def breve() -> str:
    """Identidad y trato en pocas líneas, para respuestas con datos (preguntas a la memoria)."""
    p = get()
    return " ".join(p["identidad"]["descripcion"].split()) + " " + " ".join(p["trato"]["reglas"])


# ---------- dosificar: tratamientos y emojis ----------

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u23E9-\u23FA\u231A\u231B]\uFE0F?"
    "(?:\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF]\uFE0F?)*"
)
EMOJI_CADA = 3  # como mucho un mensaje con emoji de cada 3
NO_VOCATIVO = {"el", "la", "los", "su", "sus", "tu", "mi", "un", "una", "del", "al", "de", "como", "otro", "ese", "este"}
_historial: deque = deque(maxlen=EMOJI_CADA)  # (tratamiento usado o None, hubo emoji)
_ultimo_trato: str | None = None


def reiniciar() -> None:
    """Olvida los mensajes recientes (para pruebas)."""
    global _ultimo_trato
    _historial.clear()
    _ultimo_trato = None
    _ultima.clear()


def _re_trato() -> re.Pattern:
    nombres = sorted(get()["trato"]["tratamientos"], key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in nombres) + r")(?!\w)", re.I)


def _vocativos(texto: str) -> list[re.Match]:
    """Tratamientos usados para dirigirse a él («Listo, jefe.», «Jefe, ya es hora»), no «su jefe»."""
    out = []
    for m in _re_trato().finditer(texto):
        antes = texto[: m.start()].rstrip()
        palabra = re.search(r"(\w+)$", antes)
        if palabra and palabra.group(1).lower() in NO_VOCATIVO:
            continue
        despues = texto[m.end():].lstrip()
        if not despues or despues[0] in ",.;:!?()…":
            out.append(m)
    return out


def _quitar(texto: str, m: re.Match) -> str:
    """Quita un vocativo con su coma: «Listo, jefe: x» -> «Listo: x»; «Jefe, ya» -> «Ya»."""
    a, b = m.start(), m.end()
    antes = texto[:a].rstrip()
    if antes.endswith(","):
        resto = texto[b:]
        if re.match(r",\s+(?:y|e|o|u|pero|que)\b", resto):  # «tu foto, jefe, y ya» -> «tu foto y ya»
            resto = resto[1:]
        return antes[:-1] + resto
    if antes and (antes[-1].isalnum()):  # «Hola jefe.»
        return antes + texto[b:]
    resto = re.sub(r"^\s*,\s*", "", texto[b:])
    nuevo = texto[:a] + resto
    i = a + next((j for j, c in enumerate(resto) if c.isalpha()), 0)
    return nuevo[:i] + nuevo[i:i + 1].upper() + nuevo[i + 1:]


def dosificar(texto: str) -> str:
    """Aplica los límites de tratamientos y emojis a un mensaje que va a salir, y lo anota."""
    global _ultimo_trato
    if not texto:
        return texto
    previo = _historial[-1] if _historial else (None, False)
    # Emojis: el primero, si ninguno de los 2 mensajes anteriores llevó
    con_emoji = any(e for _, e in list(_historial)[-(EMOJI_CADA - 1):])
    emojis = list(EMOJI.finditer(texto))
    for i, m in reversed(list(enumerate(emojis))):
        if i > 0 or con_emoji:
            texto = texto[: m.start()] + texto[m.end():]
    puso_emoji = bool(emojis) and not con_emoji
    # Tratamiento: solo si el anterior no llevó; uno solo y distinto del último usado
    vocs = _vocativos(texto)
    usado = None
    if vocs and previo[0] is None:
        primero = vocs[0]
        for m in reversed(vocs[1:]):
            texto = _quitar(texto, m)
        usado = primero.group(1).lower()
        if usado == _ultimo_trato:
            otro = tratamiento()
            otro = otro[0].upper() + otro[1:] if primero.group(1)[0].isupper() else otro
            texto = texto[: primero.start()] + otro + texto[primero.end():]
            usado = otro.lower()
        _ultimo_trato = usado
    else:
        for m in reversed(vocs):
            texto = _quitar(texto, m)
    _historial.append((usado, puso_emoji))
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"[ \t]+([.,;:!?])", r"\1", texto)
    return re.sub(r"(?m)^[ \t]+", "", texto).strip()
