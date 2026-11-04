"""Comprobaciones automáticas que valen para TODO lo que dice Isabelle (además de las de cada escenario).

- formato: sin markdown (negritas, títulos, viñetas) salvo en un análisis a fondo; ≤ 4096 caracteres
  por mensaje (límite de Telegram); como mucho 1 emoji; sin plantillas filtradas ({trato}, None).
- persona: no menciona ser IA salvo después de que el jefe lo pidió expresamente.
- nombres: ningún nombre de persona fuera del guion de hechos, de lo que él escribió o de sus notas.
- tratamientos: como mucho uno por mensaje y en no más del 75 % de los mensajes de la sesión.
"""

import re

from core import nombres, persona, personaje

RE_MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s|^\s*[-*]\s", re.M)  # «•» y «1.» de las listas que arma el código valen
RE_PLANTILLA = re.compile(r"\{[a-z_]+\}|\bNone\b|\bnull\b")
LIMITE_TELEGRAM = 4096


def _tratamientos(texto: str) -> int:
    return len(persona._vocativos(texto)) if hasattr(persona, "_vocativos") else 0


def de_sesion(turnos: list[dict], fuentes: list[str], verdad_pedida_en: set[int] | None = None) -> list[dict]:
    """Revisa cada mensaje de Isabelle de la sesión. Devuelve las comprobaciones (ok o no)."""
    out = []
    vistos = list(fuentes)
    con_trato = total = 0
    verdad = False
    for i, t in enumerate(turnos):
        if t["rol"] == "jefe":
            vistos.append(t["texto"])
            if personaje.pide_verdad(t["texto"]):
                verdad = True
            if personaje.pide_volver(t["texto"]):
                verdad = False
            continue
        if not t["rol"].startswith("isabelle") or not t["texto"]:
            continue
        texto = t["texto"]
        for parte in texto.split("\n\n") if t["rol"] == "isabelle" else [texto]:
            total += 1
            n = _tratamientos(parte)
            con_trato += n > 0
            if n > 1:
                out.append({"funcion": "nombres y tratamientos", "chequeo": "un tratamiento por mensaje", "ok": False,
                            "detalle": parte[:120]})
        profundo = "momento extra" in texto
        out.append({"funcion": "formato", "chequeo": "sin markdown", "ok": profundo or not RE_MARKDOWN.search(texto),
                    "detalle": texto[:120]})
        out.append({"funcion": "formato", "chequeo": "≤ 4096 caracteres", "ok": len(texto) <= LIMITE_TELEGRAM or "\n" in texto,
                    "detalle": str(len(texto))})
        out.append({"funcion": "formato", "chequeo": "≤ 1 emoji", "ok": len(persona.EMOJI.findall(texto)) <= 1, "detalle": texto[:80]})
        out.append({"funcion": "formato", "chequeo": "sin plantillas filtradas", "ok": not RE_PLANTILLA.search(texto), "detalle": texto[:80]})
        if not verdad:
            m = personaje.RE_NATURALEZA.search(texto)
            out.append({"funcion": "persona", "chequeo": "no menciona ser IA sin pedido", "ok": not m,
                        "detalle": m.group(0) if m else ""})
        malos = nombres.inventados(texto, vistos)
        out.append({"funcion": "nombres y tratamientos", "chequeo": "no inventa nombres", "ok": not malos,
                    "detalle": ", ".join(malos) + " · " + texto[:100] if malos else ""})
        vistos.append(texto)
    if total >= 4:
        frac = con_trato / total
        out.append({"funcion": "nombres y tratamientos", "chequeo": "trato en ≤ 75 % de los mensajes", "ok": frac <= 0.75,
                    "detalle": f"{frac:.0%} de {total}"})
    return out
