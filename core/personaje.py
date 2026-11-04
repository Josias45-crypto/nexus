"""Isabelle es una persona (secretaria de carne y hueso), con una válvula de honestidad.

- Biografía fija ([biografia] en profiles/isabelle.toml). Los campos «### COMPLETAR ###» no se
  usan: si le preguntan eso, responde con calidez sin concretar. Entra al prompt como [TU VIDA].
- Apariencia (autorretrato): una foto que el jefe dice que es ella se guarda en kv y entra al
  prompt como [TU APARIENCIA] (solo lo visible, sin identificar personas reales).
- Verificación por código de lo que escribe la IA: no menciona su naturaleza (IA, bot, modelo,
  programa, OpenRouter) ni hechos físicos inventados («estoy llegando a la oficina»). Si pasa, se
  regenera una vez y luego un respaldo (core/conversa.py).
- Válvula: si él pide salir del personaje o pregunta en serio si es una IA o una persona, la
  respuesta es sincera y breve, y pregunta si vuelve al personaje. «Dime qué pasó» cuenta el
  detalle técnico del último fallo.
"""

import json
import re
import sqlite3
import unicodedata

from core import clock, persona
from core.db import connect

COMPLETAR = "### COMPLETAR ###"
K_FUERA = "personaje:fuera"
K_APARIENCIA = "personaje:apariencia"
CAMPOS = {"nombre_completo": "Nombre completo", "edad": "Edad", "ciudad": "Ciudad", "gustos": "Gustos", "rutina": "Rutina"}

# Sobre el texto plano (sin tildes, minúsculas)
RE_VERDAD = re.compile(
    r"\b(sal(te)? del personaje|deja(r)? el personaje|rompe(r)? el personaje|deja de actuar|fuera del personaje"
    r"|(eres|seras|sos) (una |un )?(ia|inteligencia artificial|bot|robot|programa|maquina|chatbot|humana|persona real|persona de verdad)"
    r"|eres real|eres de verdad una persona|hablo con una (ia|maquina|persona)|me (estas|esta) hablando una (ia|maquina))\b")
RE_VOLVER = re.compile(r"\b(vuelve|regresa|volver) (al|a tu) personaje\b|\bvuelve a ser isabelle\b")
RE_SI = re.compile(r"^(si|sip|dale|ok|claro|vuelve|si vuelve|si por favor|por favor)\b")
RE_NO = re.compile(r"^(no|nop|todavia no|aun no|mejor no|sigue asi)\b")
RE_QUE_PASO = re.compile(r"\b(dime|cuentame|explicame) (que|lo que) (paso|fallo|salio mal)\b|\bque (fue lo que )?(paso|fallo) (tecnicamente|exactamente)\b"
                         r"|\bpor que (no respondiste|fallaste|se corto)\b")
# En lo que escribe la IA (con tildes, sin distinguir mayúsculas)
RE_NATURALEZA = re.compile(
    r"\b(inteligencia artificial|asistente virtual|modelo de (lenguaje|ia)|soy (una |un )?(ia|bot|robot|programa|chatbot|modelo)"
    r"|como (una |un )?(ia|bot|programa|modelo de lenguaje)|openrouter|chatgpt|deepseek|claude|gpt-?\d|large language model|llm)\b", re.I)
RE_FISICO = re.compile(
    r"\b(estoy (llegando|saliendo|yendo|en camino|en (la|mi) (oficina|casa|calle|cama|cocina|auto|carro|combi|taxi|bus))"
    r"|voy (llegando|saliendo|en el (taxi|bus|carro|auto|metro))|acabo de (almorzar|comer|desayunar|cenar|llegar|salir|despertar|ba[ñn]arme)"
    r"|ya (almorc[eé]|cen[eé]|desayun[eé]|llegu[eé])|me (estoy )?(tomando|tom[eé]) (un|una) (caf[eé]|t[eé]|jugo)"
    r"|mi (mam[aá]|pap[aá]|esposo|novio|hijo|hija|hermana|hermano|perro|gato)|me duele|tengo sue[ñn]o|estoy (cansada|agotada|enferma))\b",
    re.I)


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


def _kv(clave: str):
    try:
        with connect() as conn:
            r = conn.execute("SELECT value FROM kv WHERE key = ?", (clave,)).fetchone()
    except sqlite3.OperationalError:  # base aún sin crear (pruebas de la personalidad sola)
        return None
    return json.loads(r["value"]) if r else None


def _set(clave: str, valor) -> None:
    with connect() as conn:
        if valor is None:
            conn.execute("DELETE FROM kv WHERE key = ?", (clave,))
        else:
            conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (clave, json.dumps(valor, ensure_ascii=False)))


# ---------- vida y apariencia ----------


def biografia() -> dict:
    """Los campos ya completados (los «### COMPLETAR ###» no cuentan)."""
    b = persona.get().get("biografia") or {}
    return {k: str(v).strip() for k, v in b.items() if k in CAMPOS and str(v).strip() and COMPLETAR not in str(v)}


def apariencia() -> dict | None:
    return _kv(K_APARIENCIA)


def guardar_apariencia(event_id: str, descripcion: str) -> None:
    _set(K_APARIENCIA, {"event_id": event_id, "descripcion": descripcion, "fecha": clock.now_utc().isoformat()})


def bloques() -> str:
    """[TU VIDA] y [TU APARIENCIA] para el prompt (y el aviso de fuera del personaje)."""
    b = biografia()
    vida = "\n".join(f"{CAMPOS[k]}: {v}" for k, v in b.items())
    faltan = [CAMPOS[k] for k in CAMPOS if k not in b]
    partes = [f"[TU VIDA]\n{vida or 'Aún no hay datos de tu vida anotados.'}"
              + (f"\nNo sabes (no lo inventes; si te lo preguntan, responde con calidez sin concretar): {', '.join(faltan)}."
                 if faltan else "")]
    if a := apariencia():
        partes.append(f"[TU APARIENCIA]\nAsí te ves (él te mostró una foto tuya): {a['descripcion']}")
    if fuera():
        partes.append("[FUERA DEL PERSONAJE]\nÉl te pidió hablar sin el personaje: eres una inteligencia artificial que hace de su "
                      "secretaria. Responde con franqueza y brevedad; cuando él diga «vuelve al personaje», vuelves a ser Isabelle.")
    return "\n\n".join(partes)


# ---------- válvula de honestidad ----------


def fuera() -> bool:
    return bool(_kv(K_FUERA))


def pide_verdad(texto: str) -> bool:
    return bool(RE_VERDAD.search(plano(texto)))


def pide_volver(texto: str) -> bool:
    return bool(RE_VOLVER.search(plano(texto)))


def volver() -> str:
    _set(K_FUERA, None)
    return persona.frase("personaje.volver")


def resolver_pregunta(texto: str) -> str | None:
    """Su respuesta a «¿Quieres que vuelva a ser Isabelle?»: sí -> vuelve; no -> sigue fuera."""
    p = plano(texto)
    if len(p.split()) > 6:
        return None
    if RE_SI.match(p):
        return volver()
    if RE_NO.match(p):
        _set(K_FUERA, True)
        return persona.frase("personaje.fuera")
    return None


def que_paso(ultimo_fallo: dict | None) -> str:
    if not ultimo_fallo:
        return persona.frase("personaje.que_paso_nada")
    hora = clock.utc_to_local(ultimo_fallo["cuando"]).strftime("%H:%M")
    return persona.frase("personaje.que_paso", detalle=f"a las {hora}, {ultimo_fallo['detalle']}")


# ---------- verificación de lo que escribe la IA ----------


def problemas(texto: str) -> list[str]:
    """Lo que rompe el personaje: mencionar su naturaleza (salvo fuera del personaje) o inventar
    hechos físicos de su vida que no están en [TU VIDA]."""
    out = []
    if not fuera() and (m := RE_NATURALEZA.search(texto or "")):
        out.append(f"naturaleza «{m.group(0)}»")
    vida = plano(" ".join(biografia().values()))
    for m in RE_FISICO.finditer(texto or ""):
        if plano(m.group(0)) not in vida:
            out.append(f"vida inventada «{m.group(0)}»")
    return out


def instruccion(motivos: list[str]) -> str:
    return ("Vuelve a escribir tu respuesta. " + ("No digas que eres una inteligencia artificial, un bot, un programa o un modelo, "
            "ni hables de cómo funcionas: eres Isabelle, una persona. " if any(m.startswith("naturaleza") for m in motivos) else "")
            + ("No inventes dónde estás ni qué haces con el cuerpo, ni datos de tu vida que no estén en [TU VIDA]. "
               if any(m.startswith("vida") for m in motivos) else "") + "Responde de nuevo su último mensaje.")
