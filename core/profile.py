"""Perfil de rubro (profiles/<NEXUS_PROFILE>.toml).

El núcleo es genérico: nombre, persona, tono y reglas proactivas de cada negocio salen del
perfil. Agregar un rubro = agregar un .toml, sin tocar el código.
"""

import logging
import re
import tomllib
from functools import lru_cache
from pathlib import Path

from config import settings

log = logging.getLogger("nexus.profile")

PROFILES_DIR = Path(settings.PROFILES_DIR)

DEFAULTS = {
    "asistente": {
        "nombre": "NEXUS",
        "idioma": "es",
        "tono": "claro y breve",
        "persona": "Eres la memoria personal de tu dueño.",
    },
    "negocio": {"descripcion": ""},
    "entidades": {"categorias": []},
    "proactivo": {"resumen_matutino": "", "seguimiento_dias": 0},
    "ejemplos": {"preguntas": []},
}


class ProfileError(ValueError):
    pass


def _validate(data: dict, name: str) -> dict:
    merged = {sec: {**vals, **data.get(sec, {})} for sec, vals in DEFAULTS.items()}
    hora = merged["proactivo"]["resumen_matutino"]
    if hora and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora):
        raise ProfileError(f"Perfil '{name}': resumen_matutino debe ser HH:MM o vacío, no '{hora}'")
    if not isinstance(merged["entidades"]["categorias"], list):
        raise ProfileError(f"Perfil '{name}': entidades.categorias debe ser una lista")
    merged["id"] = name
    return merged


@lru_cache
def get() -> dict:
    name = settings.PROFILE
    if not re.fullmatch(r"[a-z0-9_\-]+", name):
        raise ProfileError(f"NEXUS_PROFILE inválido: '{name}' (solo minúsculas, números, - y _)")
    path = PROFILES_DIR / f"{name}.toml"
    if not path.is_file():
        disponibles = sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))
        raise ProfileError(f"No existe el perfil '{name}'. Disponibles: {', '.join(disponibles)}")
    with path.open("rb") as f:
        profile = _validate(tomllib.load(f), name)
    log.info("Perfil activo: %s (%s)", name, profile["asistente"]["nombre"])
    return profile


def system_prompt() -> str:
    """Encabezado de sistema común: quién es el asistente y para qué negocio."""
    p = get()
    a, negocio = p["asistente"], p["negocio"]["descripcion"]
    parts = [f"Te llamas {a['nombre']}. {a['persona'].strip()}"]
    if negocio:
        parts.append(f"Contexto del negocio: {negocio}")
    parts.append(f"Tono: {a['tono']}.")
    return " ".join(" ".join(parts).split())
