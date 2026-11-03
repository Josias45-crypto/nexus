"""Búsqueda en internet (Fase 2E), por OpenRouter con el plugin «web» (buscador Exa).

Niveles: primero el cerebro; luego el saber general del modelo (avisando que no sale del cerebro);
internet solo si el dato no está en el cerebro y es reciente, local o el modelo no lo sabe, o si
el jefe lo pide («busca en internet…»). «No busques en internet» se respeta en esa pregunta. Las
reglas de aquí son baratas (sin IA).

Privacidad: zdr y data_collection=deny cubren al modelo, no al buscador. Por eso a internet viaja
solo la pregunta reformulada y limpia (consulta_limpia): sin teléfonos, correos, montos, números
largos ni nombres de su ficha o de sus notas; el mensaje de sistema no lleva nada del jefe.

La respuesta cita 1 a 3 fuentes legibles (sitio y dominio) y el código verifica que cada nombre y
cifra salga de lo que devolvió la búsqueda; si no, lo marca. Tope diario en [internet] de
profiles/isabelle.toml, con contador en la tabla busquedas (sin la consulta).
"""

import logging
import re
import unicodedata
from urllib.parse import urlparse

from core import charla, clock, persona, verify
from core.db import connect
from providers.openrouter import get_client

log = logging.getLogger("nexus.internet")

FUENTES_MAX = 3
RUIDO = {"exa.ai", "openrouter.ai"}
TOKENS = 500
TEMPERATURA = 0.2

RE_PIDE = re.compile(
    r"\b(?:busca(?:lo|me|melo)?|buscar|investiga(?:lo)?|averigua(?:lo|me)?|consulta(?:lo)?|revisa)\b"
    r"(?:\s+(?:por favor|porfa))?\s+(?:en\s+)?(?:internet|la web|la red|google|online|en linea)\b"
    r"|\b(?:en|por)\s+internet\b")
RE_NO_BUSCAR = re.compile(r"\b(no (lo )?busques|no (uses|vayas a|entres a) internet|sin (buscar en )?internet"
                          r"|no (lo )?busques en internet|no hace falta buscar)\b")
RE_RECIENTE = re.compile(
    r"\b(hoy|ayer|esta semana|este mes|este ano|actual|actuales|actualmente|ahora mismo|ultim[oa]s?|reciente[s]?"
    r"|recientemente|noticias?|precio[s]? (de|del)|cuanto (cuesta|esta|vale|cobra)|tipo de cambio|cotizacion"
    r"|clima|pronostico|horario[s]? de|esta abierto|a que hora (abre|cierra)|cerca de|direccion de"
    r"|telefono de|donde (queda|esta|hay|venden)|quien gano|resultado[s]? de|20(?:2[4-9]|3\d))\b")
RE_PERSONAL = re.compile(r"\b(me|mi|mis|mio|mia|conmigo|nuestro|nuestra|nuestros|mi negocio|mis clientes)\b")
RE_EMAIL = re.compile(r"\S+@\S+\.\S+")
RE_TELEFONO = re.compile(r"\+?\d[\d\s-]{6,}\d")
RE_MONTO = re.compile(r"(?:S/\.?|US\$|\$|€)\s?\d[\d.,]*|\d[\d.,]*\s*(?:soles|sol|dólares|dolares|usd|pen|lucas)\b", re.I)
RE_LARGO = re.compile(r"\b\d{8,}\b")
RE_MAYUS = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+")
RE_LINK = re.compile(r"\(?\[([^\]]+)\]\((https?://[^)\s]+)\)\)?")
SISTEMA = (
    "Respondes en español del Perú, en 2 a 4 frases, usando solo los resultados de la búsqueda. Di de qué "
    "sitio sale cada dato («según …»). Si algo es suposición tuya y no está en los resultados, empiézalo con "
    "«Supongo que». Si los resultados no coinciden o no son claros, dilo con «No está claro». Sin listas, sin "
    "enlaces y sin markdown.")


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


def config() -> dict:
    c = persona.get().get("internet")
    if not isinstance(c, dict) or not isinstance(c.get("busquedas_dia"), int):
        raise persona.PersonaError(f"{persona.ARCHIVO}: falta [internet] con busquedas_dia")
    return c


def texto(clave: str, **datos) -> str:
    import random

    v = config()[clave]
    v = random.choice(v) if isinstance(v, list) else v
    return v.format(trato=persona.tratamiento(), **datos)


# ---------- decidir ----------


def pedida(texto_: str) -> bool:
    """«Busca en internet…», «averígualo en internet»."""
    return bool(RE_PIDE.search(plano(texto_))) and not no_buscar(texto_)


def no_buscar(texto_: str) -> bool:
    return bool(RE_NO_BUSCAR.search(plano(texto_)))


def conviene(pregunta: str) -> bool:
    """Sin el dato en el cerebro: ¿vale la pena internet? Reciente o local, y no sobre él."""
    p = plano(pregunta)
    return bool(RE_RECIENTE.search(p)) and not RE_PERSONAL.search(p) and not no_buscar(pregunta)


def hechas_hoy() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM busquedas WHERE dia = ?",
                            (clock.now_local().date().isoformat(),)).fetchone()[0]


def queda_cupo() -> bool:
    return hechas_hoy() < config()["busquedas_dia"]


# ---------- privacidad ----------


def _nombres_privados() -> set[str]:
    """Nombres que no deben salir: los de su ficha, sus tratamientos y los de varias palabras que
    aparecen en sus notas (clientes, proveedores...)."""
    nombres = {"josias", "joel", "isabelle"}
    for item in charla.ficha().get("personas", []):
        nombre = str(item).split(":")[0].strip()
        if nombre:
            nombres.add(plano(nombre))
            nombres.update(w for w in plano(nombre).split() if len(w) >= 3)
    return nombres


def _en_sus_notas(nombre: str) -> bool:
    palabras = [w for w in re.findall(r"\w+", nombre) if w]
    if not palabras:
        return False
    with connect() as conn:
        r = conn.execute("SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1",
                         (" ".join(f'"{w}"' for w in palabras),)).fetchone()
    return bool(r)


def consulta_limpia(pregunta: str) -> str | None:
    """La pregunta como viaja a internet: sin el pedido de buscar, sin teléfonos, correos, montos,
    números largos ni nombres privados. None si no queda una pregunta útil."""
    t = RE_PIDE.sub(" ", pregunta)
    t = re.sub(r"(?i)\b(isabelle|por favor|porfa|jefe)\b", " ", t)
    for rx in (RE_EMAIL, RE_MONTO, RE_TELEFONO, RE_LARGO):
        t = rx.sub(" ", t)
    for m in list(RE_MAYUS.finditer(t)):
        if _en_sus_notas(m.group(0)):
            t = t.replace(m.group(0), " ")
    privados = _nombres_privados()
    palabras = [w for w in re.findall(r"[\wáéíóúñÁÉÍÓÚÑ¿?¡!.,%-]+", t) if plano(w) not in privados or not plano(w)]
    t = " ".join(palabras)
    t = re.sub(r"\s+([,.?!])", r"\1", t)
    t = re.sub(r"^[\s,.:;-]+|[\s,:;-]+$", "", t).strip()
    t = re.sub(r"^(?:y|que|de|sobre|el|la)\s+(?=\w)", "", t, flags=re.I) if len(t.split()) > 3 else t
    return t if len([w for w in plano(t).split() if len(w) > 2]) >= 2 else None


# ---------- buscar ----------


def sitio(titulo: str, url: str) -> tuple[str, str]:
    """(nombre del sitio, dominio): «Tipo de cambio | SUNAT», sunat.gob.pe -> («SUNAT», «sunat.gob.pe»)."""
    dominio = (urlparse(url).netloc or url).lower().removeprefix("www.")
    partes = re.split(r"\s+[|\-–—]\s+", titulo or "")
    nombre = partes[-1].strip() if len(partes) > 1 and 2 <= len(partes[-1].strip()) <= 40 else ""
    if not nombre:
        base = dominio.split(".")
        nombre = (base[-3] if len(base) >= 3 and base[-2] in ("com", "gob", "gov", "org", "edu", "net", "co") else base[0]).capitalize()
    return nombre, dominio


def fuentes_legibles(fuentes: list[dict]) -> list[dict]:
    """Hasta 3 fuentes, una por dominio: [{nombre, dominio, url}]."""
    out, vistos = [], set()
    for f in fuentes:
        nombre, dominio = sitio(f.get("titulo", ""), f.get("url", ""))
        if dominio in RUIDO or re.match(r"(?i)as of \d", f.get("titulo", "")):
            continue  # marcas del buscador, no son fuentes
        if dominio and dominio not in vistos:
            vistos.add(dominio)
            out.append({"nombre": nombre, "dominio": dominio, "url": f["url"]})
        if len(out) == FUENTES_MAX:
            break
    return out


def _limpiar_respuesta(salida: str) -> str:
    salida = RE_LINK.sub(lambda m: f" {m.group(1)}", salida or "")  # «según [clarin.com](url)» -> «según clarin.com»
    salida = re.sub(r"\*\*|__|^#+\s*", "", salida, flags=re.M)
    salida = re.sub(r"\s+([,.;:])", r"\1", salida)
    return " ".join(salida.split()).strip()


async def buscar(consulta: str) -> dict:
    """Una búsqueda: {texto, fuentes, sin_verificar, costo}. La consulta ya debe venir limpia.
    Lanza ChatUnavailable si OpenRouter no responde."""
    salida, info = await get_client().chat_ex(
        [{"role": "system", "content": SISTEMA}, {"role": "user", "content": consulta}],
        temperature=TEMPERATURA, max_tokens=TOKENS, web=True)
    crudas = info.get("fuentes") or []
    with connect() as conn:
        conn.execute("INSERT INTO busquedas (created_at, dia, fuentes, costo_usd) VALUES (?, ?, ?, ?)",
                     (clock.now_utc().isoformat(), clock.now_local().date().isoformat(), len(crudas), info.get("costo_usd")))
    respuesta = _limpiar_respuesta(salida)
    textos = [f"{f.get('titulo', '')}\n{f.get('extracto', '')}" for f in crudas]
    faltan = verify.unverified(respuesta, textos, consulta) if crudas and respuesta else []
    log.info("Búsqueda en internet: %d fuentes, %d dato(s) sin verificar", len(crudas), len(faltan))
    return {"texto": respuesta, "fuentes": fuentes_legibles(crudas), "sin_verificar": faltan,
            "costo": info.get("costo_usd"), "llm": info}


def mensaje(r: dict) -> str:
    """La respuesta para el jefe: lo que encontró, lo no verificado y las fuentes legibles."""
    partes = [r["texto"]]
    if r["sin_verificar"]:
        partes.append(f"⚠ Esto no lo vi en las fuentes, tómelo con cuidado: {', '.join(r['sin_verificar'])}.")
    partes.append("Fuentes de internet: " + "; ".join(f"{f['nombre']} ({f['dominio']})" for f in r["fuentes"]) + ".")
    return "\n\n".join(partes)


def nota(pregunta: str, r: dict) -> str:
    """La nota que se guarda si el jefe dice que sí: marcada «de internet», con fuente y fecha."""
    hoy = clock.now_local()
    from core import when

    fecha = f"{hoy.day} de {when.MONTH_NAMES[hoy.month - 1]} de {hoy.year}"
    fuentes = "\n".join(f"- {f['nombre']} ({f['dominio']}): {f['url']}" for f in r["fuentes"])
    return f"[De internet, consultado el {fecha}]\nPregunta: {pregunta}\n\n{r['texto']}\n\nFuentes:\n{fuentes}"
