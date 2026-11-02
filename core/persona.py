"""Personalidad de Isabelle: profiles/isabelle.toml (editable, sin tocar el código).

sistema() arma el bloque [QUIÉN ERES] del prompt; frase() devuelve una frase fija de la
personalidad con un tratamiento que varía ("jefe", "señor Josias"...).
"""

import logging
import random
import tomllib
from functools import lru_cache
from pathlib import Path

from config import settings

log = logging.getLogger("nexus.persona")

ARCHIVO = "isabelle.toml"
SECCIONES = ("identidad", "trato", "caracter", "especialidades", "estilo", "honestidad", "frases")
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
    return random.choice(get()["trato"]["tratamientos"])


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
