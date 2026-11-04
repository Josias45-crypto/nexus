"""Conversación con Isabelle en lenguaje natural, sin comandos.

Cada turno (uno o varios mensajes seguidos del jefe, que el canal junta):
1. Se guardan en el historial (core/charla.py) y se pausa la actualización de la memoria.
2. Primero lo que espera respuesta en ese chat: «¿de la mañana o de la noche?» de una hora sin
   am/pm (si el mensaje no la responde, se descarta y el mensaje sigue como uno normal), confirmar
   un borrado, la nueva hora de un recordatorio, aclarar una hora dudosa o el "¿lo guardo?" de un
   recordatorio dudoso.
3. Reglas baratas: despedidas, saludos, "recuerda esto", recordatorios, "ya lo hice",
   "cancela el 3", "¿qué recordatorios tengo?", ayuda y "analiza a fondo".
4. Si no alcanzan, la IA clasifica el mensaje en JSON {"accion", "consulta"}. Se valida contra
   ACCIONES; si el JSON llega roto hay un reintento y, si vuelve a fallar, no se ejecuta nada y
   se pide repetir. Lo que la IA devuelve nunca se ejecuta sin validar: "guardar" guarda las
   palabras del jefe, no las de la IA, y "consulta" solo sirve para buscar.
5. Se ejecuta la acción; la respuesta se guarda en el historial.

Solo se pide confirmación al borrar algo o cuando la hora de un recordatorio es dudosa.
Niveles de respuesta: primero la memoria (con fuentes que arma el código); si no hay dato, el
conocimiento general del modelo, avisando que no sale de la memoria. Internet: Fase 2E.
Archivos (Fase 3, archivos()): fotos y PDF escaneados se analizan al llegar y ella los comenta;
una nota de voz corta se atiende como si él la hubiera escrito; los videos solo se guardan.
Nunca se registra el contenido de los mensajes: solo la acción, el nivel y los tamaños.
"""

import asyncio
import contextvars
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from core import ask, charla, citations, clock, filtro, inbox, iniciativa, internet, medios, nombres, olvido, pendientes, persona, processor, questions, reminders, when
from core.db import connect
from core.search import relevant, search
from providers.openrouter import ChatUnavailable, get_client
from senses import Unsupported, documentos

log = logging.getLogger("nexus.conversa")

ACCIONES = ("guardar", "preguntar", "analizar", "opinar", "charlar")
ESPERA_MIN = 15  # minutos que vale lo que espera respuesta (confirmar, nueva hora...)
FRAGMENTOS_MAX = 4
MAX_FUENTES = 3
TOKENS = {"guardado": 300, "charlar": 400, "opinar": 600, "analizar": 700}
TOKENS_PROFUNDO = 6000  # incluye el razonamiento del modelo potente
LARGO_PROFUNDO = 1500  # caracteres del análisis a fondo, salvo que pida más detalle
RE_DETALLE = re.compile(r"\b(detallad[oa]|con (mas |mucho )?detalle|completo|extens[oa]|paso a paso|todo lo que)\b")
TEMPERATURA = {"guardado": 0.8, "charlar": 0.8, "opinar": 0.8, "analizar": 0.3}

_en_curso = 0
# Lo que dice una nota de voz ya está guardado con su audio: «guardar» no lo duplica como nota
_nota_de_voz: contextvars.ContextVar[bool] = contextvars.ContextVar("nota_de_voz", default=False)


def ocupado() -> bool:
    """True mientras Isabelle prepara una respuesta (la memoria espera)."""
    return _en_curso > 0


@dataclass
class Respuesta:
    texto: str
    accion: str
    nivel: str | None = None  # memoria | general | None
    botones: list | None = None  # [[{"texto": ..., "dato": ...}]]
    llm: dict | None = field(default=None)
    seguir: bool = False  # el canal envía esto y enseguida pide la continuación (POST /conversar/seguir)

    def dict(self) -> dict:
        return asdict(self)


# ---------- texto ----------


def plano(texto: str) -> str:
    """Minúsculas, sin tildes ni signos, espacios simples: '¡Ya lo HICE!' -> 'ya lo hice'."""
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


YES = {"si", "s", "ok", "okay", "okey", "dale", "listo", "correcto", "confirmo", "claro", "yes",
       "va", "perfecto", "afirmativo", "hazlo", "guardalo", "exacto", "eso"}
NO = {"no", "n", "nop", "nel", "mejor no", "dejalo", "olvidalo", "espera"}
QUESTION_WORDS = {"que", "cuando", "como", "donde", "cual", "cuales", "quien", "cuanto", "cuanta", "porque"}


def confirmacion(texto: str) -> str:
    """'si', 'no' o 'duda' para una respuesta a "¿lo hago?"."""
    words = plano(texto).split()
    if not words or "?" in texto or "¿" in texto or set(words) & QUESTION_WORDS:
        return "duda"
    first, rest = words[0], set(words[1:])
    if first in YES and not rest & {"no"}:
        return "si"
    if first in NO or " ".join(words[:2]) in NO:
        return "no" if not rest & (YES - {"no"}) else "duda"
    return "duda"


RE_CIERRE = re.compile(
    r"^(ok+( ok+)*|okey|okis|oki|ya|listo|gracias|muchas gracias|mil gracias|grax|chau|chao|adios|bye"
    r"|hasta (luego|manana|pronto|la vista)|nos vemos|me voy|ya me voy|cuidate|perfecto|de acuerdo"
    r"|vale|entendido|genial|excelente|super)( (gracias|isabelle|pues|ok+|nomas|entonces))*$"
)
RE_DESPEDIDA = re.compile(
    r"\b(hasta (manana|luego|pronto|la proxima)|nos vemos|me voy a (dormir|descansar|acostar)|buenas noches"
    r"|chau|chao|adios|bye|me despido|cuidate)\b"
)
RE_SALUDO = re.compile(
    r"^(start|hola|holi|holis|buenas|buen dia|buenos dias|buenas tardes|buenas noches|hey|que tal"
    r"|hola isabelle|hola que tal)( isabelle)?$"
)
RE_GUARDAR = re.compile(
    r"^\s*(?:recu[eé]rd[ae]|an[oó]t[ae](?:lo)?|ap[uú]nt[ae](?:lo)?|gu[aá]rd[ae](?:lo)?)\b"
    r"(?:\s+(?:esto|eso))?(?:\s+que)?\s*[:,.\-]?\s*(?P<resto>.*)$",
    re.I | re.S,
)
RE_RECORDATORIO = re.compile(r"^\s*(recu[eé]rdame|recordarme|av[ií]same)\b", re.I)
RE_HECHO = re.compile(r"^(listo )?(ya )?(lo )?(hice|hecho|termine|cumpli|ya esta hecho|ya esta)( ya)?( isabelle)?$")
RE_CANCELAR = re.compile(r"\b(cancela|cancelar|cancelame|borra|borrar|elimina|eliminar)\b.*?\b(\d{1,6})\b")
RE_LISTA = re.compile(r"\b(que|cuales|mis|ver) recordatorios\b|\brecordatorios (tengo|pendientes|hay)\b|\bque tengo pendiente\b")
RE_AYUDA = re.compile(r"^(ayuda|help|que puedes hacer|que sabes hacer|como funcionas|como te uso)( isabelle)?$")
# «Vuelve a mirar la foto», «léela de nuevo», «escucha otra vez el audio»: lo único que hace mirar
# otra vez un original ya analizado
RE_REVER = re.compile(
    r"\b(?:vuelve|volver) a (?:ver|mirar|leer|revisar|escuchar|oir|analizar)(?:la|lo)?\b"
    r"|\b(?:mira|mirala|miralo|revisa|revisala|revisalo|lee|leela|leelo|escucha|escuchala|escuchalo|analiza|analizala"
    r"|analizalo)\b(?:\s+\w+){0,3}\s+(?:otra vez|de nuevo|nuevamente)\b")
RE_TIPO_MEDIO = {"foto": "foto", "imagen": "foto", "boleta": "foto", "factura": "foto", "captura": "foto",
                 "documento": "pdf", "pdf": "pdf", "escaneo": "pdf", "audio": "audio", "nota de voz": "audio"}
RE_VIDEO = re.compile(r"\b(?:que|de que)\b.*\bvideo\b|\bvideo\b.*\b(?:dice|tiene|hay|sale|salia|muestra|trata|contiene|habla)\b")
RE_PROFUNDO = re.compile(
    r"\b(analiza(lo|la)? a fondo|analisis a fondo|piensa(lo|la)? bien|pienselo bien|piensalo a fondo"
    r"|dame tu opinion|cual es tu opinion|tu opinion sincera|que opinas a fondo)\b"
)


# ---------- lo que espera respuesta (en kv, sobrevive a reinicios) ----------


def _clave(channel: str, chat_id: str) -> str:
    return f"espera:{channel}:{chat_id}"


def _espera(channel: str, chat_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT value FROM kv WHERE key = ?", (_clave(channel, chat_id),)).fetchone()
    if not r:
        return None
    e = json.loads(r["value"])
    if e.get("expira", "") <= clock.now_utc().isoformat():
        _limpiar_espera(channel, chat_id)
        return None
    return e


def _esperar(channel: str, chat_id: str, **datos) -> None:
    datos["expira"] = (clock.now_utc() + timedelta(minutes=ESPERA_MIN)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_clave(channel, chat_id), json.dumps(datos, ensure_ascii=False)),
        )


def _limpiar_espera(channel: str, chat_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM kv WHERE key = ?", (_clave(channel, chat_id),))


# ---------- recordatorios ----------


def cuando(due_local: str, regla: dict | None = None) -> str:
    """'2026-11-02 16:17' -> 'hoy a las 4:17 p. m.', 'mañana martes 3 a las 9:00 a. m.'; con regla,
    'todos los lunes a las 8:00 a. m. (el primero, mañana lunes 9)'."""
    dt = datetime.strptime(due_local, "%Y-%m-%d %H:%M")
    now = clock.now_local()
    hora = when.ampm(dt)
    a_las = f"a la {hora}" if hora.startswith("1:") else f"a las {hora}"
    delta = (dt.date() - now.date()).days
    if delta == 0:
        dia = "hoy"
    else:
        rel = {1: "mañana ", 2: "pasado mañana "}.get(delta, "")
        mes = "" if rel else f" de {when.MONTH_NAMES[dt.month - 1]}"
        dia = f"{rel}{when.WEEKDAY_NAMES[dt.weekday()]} {dt.day}{mes}"
    if regla:
        return f"{when.describir_regla(regla)} {a_las} (el primero, {dia})"
    return f"{dia} {a_las}"


RE_HORA_DUDOSA = re.compile(
    r"\ba\s+las?\s+\d{1,2}(?:(?:\s*[:.h]\s*|\s+y\s+|\s+)\d{1,2}(?!\d))?(?:\s*(?:a\.?\s*m\.?|p\.?\s*m\.?))?",
    re.I,
)


def aclarar(original: str, respuesta: str) -> str:
    """Pone la hora aclarada en lugar de la dudosa: ('recuérdame hoy a las 4 17 llamar a Piter',
    '4:17 pm') -> 'recuérdame hoy a las 4:17 pm llamar a Piter'."""
    hora = re.sub(r"^\s*(?:a\s+)?las?\s+", "", respuesta.strip(), flags=re.I)
    nuevo, n = RE_HORA_DUDOSA.subn(f"a las {hora}", original, count=1)
    return nuevo if n else f"{original} {respuesta}"


RE_DIA = re.compile(
    r"\b(hoy|manana|pasado|lunes|martes|miercoles|jueves|viernes|sabado|domingo|semana|mes"
    r"|minutos?|horas?|dias?|\d{1,2} de|el \d{1,2})\b"
)
RE_POSPONER = re.compile(
    r"\b(pospon\w*|posterga\w*|aplaza\w*|mueve(?:lo)?|corre(?:lo)?|retrasa(?:lo)?)\b"
    r"(?:\s+el)?(?:\s+recordatorio)?(?:\s+n(?:ro|o|umero)?)?\s+(?P<id>\d{1,6})\b(?:\s+(?:para|a))?(?P<resto>.*)$"
)
RE_SIN_EN = re.compile(r"^\s*(un|una|dos|tres|cuatro|cinco|diez|quince|veinte|treinta|media|\d{1,3})\s+"
                       r"(minutos?|mins?|horas?|hora|dias?|semanas?)\b")
OPCIONES_POSPONER = {"1h": ("En 1 hora", "en 1 hora"), "manana9": ("Mañana 9:00 a. m.", "mañana a las 9 am"),
                     "1d": ("En 1 día", "en 1 día")}


def _botones(rid: int) -> list:
    return [[{"texto": "Cambiar", "dato": f"rec:cambiar:{rid}"}, {"texto": "Cancelar", "dato": f"rec:cancelar:{rid}"}]]


def _listo(r: dict) -> Respuesta:
    texto = persona.frase("recordatorios.creado", cuando=cuando(r["due_local"], r.get("regla")), tarea=r["text"])
    return Respuesta(texto, "recordatorio", botones=_botones(r["id"]))


def _activo(rid: int, chat_id: str, sin_confirmar: bool = True) -> dict | None:
    """El recordatorio si sigue abierto (o sin confirmar) y es de este chat."""
    r = reminders.get(rid)
    estados = reminders.ABIERTOS + (("sin_confirmar",) if sin_confirmar else ())
    if not r or r["estado"] not in estados or (r["chat_id"] and r["chat_id"] != chat_id):
        return None
    return r


def _preguntar_ampm(channel: str, chat_id: str, datos: dict) -> Respuesta:
    """«¿Las 7 de la mañana o de la noche, jefe?», por texto y sin botones; la respuesta escrita
    la entiende _resolver_hora."""
    _limpiar_espera(channel, chat_id)
    reminders.preguntar_hora(channel, chat_id, datos)
    return Respuesta(f"¿{when.pregunta_ampm(datos['h'], datos['m'])}, {persona.tratamiento()}?", "recordatorio")


async def _resolver_hora(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """Respuesta a «¿de la mañana o de la noche?». Si el mensaje no la responde, la pregunta se
    descarta y devuelve None: el mensaje se atiende como uno normal (nunca se lo traga)."""
    datos = reminders.pregunta_hora(channel, chat_id)
    if not datos:
        return None
    reminders.olvidar_pregunta_hora(channel, chat_id)
    try:
        p = reminders.resolver_hora(datos, texto)
    except reminders.NotUnderstood as exc:
        return Respuesta(str(exc), "recordatorio")
    if p is None:
        log.info("La respuesta no era la hora pendiente: se descarta la pregunta")
        return None
    if datos.get("tipo") == "posponer":
        r = _activo(int(datos["id"]), chat_id)
        nuevo = reminders.posponer(r["id"], p["due_at"], chat_id) if r else None
        if not nuevo:
            return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
        texto = persona.frase("recordatorios.pospuesto", cuando=cuando(nuevo["due_local"]), tarea=nuevo["text"])
        return Respuesta(texto, "recordatorio", botones=_botones(nuevo["id"]))
    return _listo(reminders.create(p["tarea"], p["due_at"], channel, chat_id, regla=p.get("regla")))


async def _recordatorio(texto: str, channel: str, chat_id: str) -> Respuesta:
    try:
        p = await reminders.interpret(texto)
    except reminders.HoraAmbigua as exc:
        return _preguntar_ampm(channel, chat_id, {**exc.datos, "tipo": "nuevo"})
    except reminders.NotUnderstood as exc:
        if isinstance(exc.__cause__, when.Ambiguous):  # hora dudosa: se pregunta y se espera la aclaración
            _esperar(channel, chat_id, tipo="aclarar", texto=texto)
        return Respuesta(f"{exc}", "recordatorio")
    if p.get("aviso") or p.get("metodo") == "llm":  # dudosa: ya pasó hoy o la entendió el modelo
        reminders.set_pending(channel, chat_id, p)
        _esperar(channel, chat_id, tipo="dudosa", texto=texto)
        aviso = f"{p['aviso']} " if p.get("aviso") else ""
        pregunta = "¿Se lo guardo para mañana?" if p.get("aviso") else "¿Se lo guardo así?"
        return Respuesta(
            f"{aviso}Entendí {cuando(p['due_local'], p.get('regla'))}: {p['tarea']}. {pregunta}", "recordatorio",
            botones=[[{"texto": "Sí", "dato": "pend:si"}, {"texto": "No, otra hora", "dato": "pend:otra"}]],
        )
    return _listo(reminders.create(p["tarea"], p["due_at"], channel, chat_id, regla=p.get("regla")))


def _lista_recordatorios(chat_id: str) -> Respuesta:
    items = reminders.list_open(chat_id, sin_confirmar=True)
    if not items:
        return Respuesta(f"No tiene recordatorios pendientes, {persona.tratamiento()}.", "recordatorios")
    lineas = [f"Sus recordatorios, {persona.tratamiento()} (hora de Perú):"]
    for r in items:
        serie = f", {when.describir_regla(r['regla'])}" if r["regla"] else ""
        lineas.append(f"• n.º {r['id']} — {cuando(r['due_local'])}: {r['text']} ({r['estado_es']}{serie})")
    return Respuesta("\n".join(lineas), "recordatorios")


def _cancelar(r: dict, channel: str, chat_id: str, confirmar: bool) -> Respuesta:
    """Una serie pregunta «Solo este» o «Toda la serie»; uno suelto se cancela (o se confirma antes)."""
    if r["regla"]:
        return Respuesta(
            f"Es un recordatorio que se repite ({when.describir_regla(r['regla'])}): {r['text']}. ¿Cancelo solo este o toda la serie?",
            "recordatorio",
            botones=[[{"texto": "Solo este", "dato": f"rec:uno:{r['id']}"}, {"texto": "Toda la serie", "dato": f"rec:serie:{r['id']}"}]],
        )
    if confirmar:
        _esperar(channel, chat_id, tipo="borrar", id=r["id"])
        return Respuesta(f"¿Cancelo el recordatorio n.º {r['id']} ({cuando(r['due_local'])}: {r['text']})?", "recordatorio")
    hecho = reminders.cerrar(r["id"], "cancelado", chat_id)
    if not hecho:
        return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
    return Respuesta(persona.frase("recordatorios.cancelado", tarea=r["text"]), "recordatorio")


def _pedir_cuando(channel: str, chat_id: str, rid: int, otra_vez: bool = False) -> Respuesta:
    _esperar(channel, chat_id, tipo="posponer", id=rid)
    texto = (f"No le entendí la hora, {persona.tratamiento()}. ¿Cuándo?" if otra_vez
             else persona.frase("recordatorios.cuando"))
    botones = [[{"texto": t, "dato": f"rec:pos:{rid}:{k}"} for k, (t, _) in OPCIONES_POSPONER.items()]]
    return Respuesta(texto, "recordatorio", botones=botones)


async def _posponer(r: dict, texto: str, channel: str, chat_id: str) -> Respuesta:
    """Nueva hora en lenguaje natural (solo core/when.py, sin IA). Si no queda clara, repregunta
    con botones. «mejor a las 10» de algo que aún no sonó conserva el día."""
    resto = texto.strip()
    if RE_SIN_EN.match(plano(resto)):  # «pospón el 2 una hora» -> «en una hora»
        resto = f"en {resto}"
    mismo_dia = not RE_DIA.search(plano(resto)) and r["estado"] in ("creado", "avisado_previo", "pospuesto")
    antes = datetime.strptime(r["due_local"], "%Y-%m-%d %H:%M")
    try:
        p = await reminders.interpret(f"recuérdame {resto} {r['text']}", usar_ia=False)
    except reminders.HoraAmbigua as exc:
        datos = {**exc.datos, "tipo": "posponer", "id": r["id"]}
        if mismo_dia:  # «mejor a las 10»: ese mismo día (o el siguiente, si ya pasó)
            datos.update(fecha=antes.date().isoformat(), con_dia=False)
        return _preguntar_ampm(channel, chat_id, datos)
    except reminders.NotUnderstood:
        return _pedir_cuando(channel, chat_id, r["id"], otra_vez=True)
    _limpiar_espera(channel, chat_id)
    due_at = p["due_at"]
    if mismo_dia:
        nueva = datetime.strptime(p["due_local"], "%Y-%m-%d %H:%M")
        dia = antes.replace(hour=nueva.hour, minute=nueva.minute)
        if dia > clock.now_local():
            due_at = clock.local_to_utc(dia).isoformat()
    nuevo = reminders.posponer(r["id"], due_at, chat_id)
    if not nuevo:
        return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
    texto = persona.frase("recordatorios.pospuesto", cuando=cuando(nuevo["due_local"]), tarea=nuevo["text"])
    return Respuesta(texto, "recordatorio", botones=_botones(nuevo["id"]))


async def _resolver_espera(e: dict, texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """Respuesta a lo que se esperaba, o None si el mensaje es otra cosa (se sigue normal)."""
    tipo = e.get("tipo")
    if tipo == "borrar":
        c = confirmacion(texto)
        _limpiar_espera(channel, chat_id)
        if c == "duda":
            return None
        if c == "no":
            return Respuesta(f"Lo dejo como está, {persona.tratamiento()}.", "recordatorio")
        r = reminders.get(int(e["id"]))
        hecho = reminders.cerrar(int(e["id"]), "cancelado", chat_id)
        if not hecho:
            return Respuesta("Ese recordatorio ya no estaba activo.", "recordatorio")
        return Respuesta(persona.frase("recordatorios.cancelado", tarea=r["text"]), "recordatorio")
    if tipo == "posponer":
        if confirmacion(texto) == "no":
            _limpiar_espera(channel, chat_id)
            return Respuesta(f"Lo dejo como estaba, {persona.tratamiento()}.", "recordatorio")
        r = _activo(int(e["id"]), chat_id)
        if not r:
            _limpiar_espera(channel, chat_id)
            return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
        return await _posponer(r, texto, channel, chat_id)
    if tipo == "aclarar":
        _limpiar_espera(channel, chat_id)
        if confirmacion(texto) == "no":
            return Respuesta(f"De acuerdo, {persona.tratamiento()}, no lo guardo.", "recordatorio")
        return await _recordatorio(aclarar(e["texto"], texto), channel, chat_id)
    if tipo == "otra_hora":  # pulsó «No, otra hora»: la respuesta es la hora nueva
        _limpiar_espera(channel, chat_id)
        return await _recordatorio(aclarar(e["texto"], texto), channel, chat_id)
    if tipo == "dudosa":  # el «sí/no» por texto lo resuelve _resolver_pendiente
        return None
    _limpiar_espera(channel, chat_id)
    return None


async def _resolver_pendiente(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """El "¿se lo guardo?" de un recordatorio dudoso (pending_confirmations)."""
    p = reminders.get_pending(channel, chat_id)
    if not p or p.get("reminder_id"):
        return None
    c = confirmacion(texto)
    if c == "si":
        _limpiar_espera(channel, chat_id)
        saved = reminders.confirm(channel, chat_id)
        return _listo(saved) if saved else None
    if c == "no":
        reminders.clear_pending(channel, chat_id)
        _limpiar_espera(channel, chat_id)
        return Respuesta(f"De acuerdo, {persona.tratamiento()}, no lo guardo.", "recordatorio")
    if len(plano(texto).split()) <= 3 and not RE_RECORDATORIO.match(texto):
        return Respuesta(f"¿Se lo guardo, {persona.tratamiento()}? Dígame sí o no.", "recordatorio")
    reminders.clear_pending(channel, chat_id)  # escribió otra cosa: se sigue con eso
    _limpiar_espera(channel, chat_id)
    return None


# ---------- reglas baratas ----------


async def _reglas(textos: list[str], channel: str, chat_id: str, anterior: str | None) -> Respuesta | None:
    texto = "\n".join(textos).strip()
    p = plano(texto)
    if RE_CIERRE.match(p) or (RE_DESPEDIDA.search(p) and len(p.split()) <= 8 and "?" not in texto):
        if re.search(r"\b(dormir|descansar|acostar|noches|hasta manana)\b", p):
            clave = "noche"
        elif RE_DESPEDIDA.search(p):
            clave = "despedida"
        elif re.search(r"\b(gracias|grax)\b", p):
            clave = "gracias"
        else:
            clave = "cierre"
        return Respuesta(persona.frase(clave), "cierre")
    if RE_AYUDA.match(p):
        return Respuesta(persona.frase("ayuda"), "ayuda")
    if r := iniciativa.ajuste(texto):  # «mañana me levanto a las 5», «desde hoy el saludo a las 6»
        return Respuesta(r, "iniciativa")
    if RE_RECORDATORIO.match(texto):
        return await _recordatorio(texto, channel, chat_id)
    if RE_LISTA.search(p):
        return _lista_recordatorios(chat_id)
    if RE_HECHO.match(p):
        r = reminders.ultimo_avisado(chat_id)
        if r and reminders.cerrar(r["id"], "hecho", chat_id, "ya lo hice"):
            return Respuesta(persona.frase("recordatorios.hecho", tarea=r["text"]), "recordatorio")
        return Respuesta(f"No tengo ningún recordatorio esperando su respuesta, {persona.tratamiento()}.", "recordatorio")
    if m := RE_POSPONER.search(p):
        r = _activo(int(m.group("id")), chat_id)
        if not r:
            return Respuesta(f"No encuentro un recordatorio activo con el n.º {m.group('id')}, {persona.tratamiento()}.", "recordatorio")
        resto = m.group("resto").strip()
        return await _posponer(r, resto, channel, chat_id) if resto else _pedir_cuando(channel, chat_id, r["id"])
    if m := RE_CANCELAR.search(p):
        if "recordatorio" in p or len(p.split()) <= 4:
            r = _activo(int(m.group(2)), chat_id, sin_confirmar=False)
            if not r:
                return Respuesta(f"No encuentro un recordatorio activo con el n.º {m.group(2)}, {persona.tratamiento()}.", "recordatorio")
            return _cancelar(r, channel, chat_id, confirmar=True)
    if r := olvido.pedir(texto, channel, chat_id):  # «olvida eso», «borra lo de Rosa»
        return Respuesta(r, "olvido")
    if RE_REVER.search(p):  # «vuelve a mirar la foto»: lo único que hace mirar otra vez el original
        return await _rever(p, channel, chat_id)
    if RE_VIDEO.search(p) and medios.ultimo("video"):
        return Respuesta(medios.frase("video_contenido"), "archivo")
    if m := RE_GUARDAR.match(texto):
        resto = m.group("resto").strip()
        if not resto and anterior:
            resto = anterior  # "recuerda esto" se refiere a lo último que dijo
        if not resto:
            return Respuesta(f"¿Qué quiere que guarde, {persona.tratamiento()}?", "guardar")
        return await _guardar(resto, channel)
    return None


# ---------- intérprete (IA) ----------

INTERPRETE = """Clasificas el mensaje del jefe para su secretaria Isabelle. Responde SOLO un JSON:
{"accion": "...", "consulta": "..."}
Acciones:
- guardar: le cuenta un dato estable para que lo anote (un precio, un horario, quién es alguien),
  sin anunciar una decisión ni pedir nada.
- preguntar: pregunta por un dato o un concepto: de sus notas, documentos o lo que le contó
  (clientes, deudas, precios, fechas, lo que guardó) o un saber general («¿qué es el CPC?»).
- analizar: pide revisar, comparar, calcular o evaluar algo (números, una decisión, un plan).
- opinar: pide su opinión, un consejo o ideas, o le cuenta un plan o una decisión que va a tomar
  («voy a…», «pienso…»): ella debe opinar y avisar si ve un riesgo.
- charlar: saludo, despedida, comentario, broma, cómo está, una foto, o cualquier otra cosa.
"consulta": solo para preguntar, la pregunta clara y completa en una frase (usa la conversación
si hace falta para entenderla); para las demás acciones, ""."""


def validar(datos) -> dict:
    """Valida la salida del intérprete; lanza ValueError si no sirve."""
    if not isinstance(datos, dict):
        raise ValueError("no es un objeto")
    accion = datos.get("accion")
    if accion not in ACCIONES:
        raise ValueError(f"acción desconocida: {accion!r}")
    consulta = datos.get("consulta") or ""
    if not isinstance(consulta, str):
        raise ValueError("consulta no es texto")
    return {"accion": accion, "consulta": " ".join(consulta.split())[:300]}


def _json(texto: str):
    texto = (texto or "").strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ValueError("no hay JSON")
    return json.loads(texto[inicio : fin + 1])


async def interpretar(texto: str, previos: list[dict]) -> dict | None:
    """{"accion", "consulta"} validado, o None si dos intentos no dieron un JSON válido."""
    contexto = "\n".join(f"{'Jefe' if m['rol'] == 'jefe' else 'Isabelle'}: {m['texto'][:300]}" for m in previos[-4:])
    contenido = (f"CONVERSACIÓN RECIENTE:\n{contexto or '(ninguna)'}\n\n" if contexto else "") + f"MENSAJE NUEVO:\n{texto}"
    for intento in (1, 2):
        salida = await get_client().chat(
            [{"role": "system", "content": INTERPRETE}, {"role": "user", "content": contenido}],
            temperature=0, max_tokens=150,
        )
        try:
            return validar(_json(salida))
        except (ValueError, TypeError) as exc:
            log.warning("Intérprete: salida inválida (%s), intento %d de 2", type(exc).__name__, intento)
    return None


# ---------- acciones ----------


async def _guardar(texto: str, channel: str) -> Respuesta:
    if _nota_de_voz.get():  # ya está guardada con su audio y su transcripción
        return Respuesta(persona.frase("guardado"), "guardar")
    r = await asyncio.to_thread(inbox.save, texto.encode("utf-8"), "nota.txt", "text/plain", channel, kind="text")
    return Respuesta(persona.frase("duplicado" if r.get("duplicate") else "guardado"), "guardar")


FUENTES = "De mi memoria:"


def _fuentes(sources: list[dict]) -> str:
    """Línea de fuentes legible para el jefe ("nota del 2 de nov sobre «Rosa Quispe…»"), armada
    por el código con la fecha y el comienzo de cada nota; nunca por el modelo."""
    ids = list(dict.fromkeys(s["event_id"] for s in sources if s.get("event_id")))
    eventos = {}
    if ids:
        with connect() as conn:
            for e in conn.execute(
                "SELECT e.id, e.created_at, (SELECT content FROM chunks c WHERE c.event_id = e.id"
                f" ORDER BY c.position LIMIT 1) AS primero FROM events e WHERE e.id IN ({','.join('?' * len(ids))})",
                ids,
            ):
                eventos[e["id"]] = e
    refs = []
    for s_ in sources:
        if not s_.get("filename"):
            continue
        e = eventos.get(s_.get("event_id"))
        cita, nombre = s_.get("cita") or "", s_["filename"]
        if not s_.get("ubicacion") and cita.startswith(f"{nombre}, "):
            s_ = {**s_, "ubicacion": cita[len(nombre) + 2:]}
        refs.append(citations.legible(
            s_["filename"], s_.get("created_at") or (e["created_at"] if e else None),
            (e["primero"] if e else None) or s_.get("content") or s_.get("snippet") or "",
            s_.get("ubicacion") or citations.location(s_.get("meta") or {}),
        ))
    refs = list(dict.fromkeys(refs))[:MAX_FUENTES]
    return f"\n\n{FUENTES} {'; '.join(refs)}." if refs else ""


RE_SEGUN = re.compile(r"^(?:seg[uú]n|de acuerdo con|conforme a)\s+(?:la|su|mi|sus|mis|las)\s+(?:notas?|apuntes?|memoria|fuentes?)\b[^,:]{0,60}[,:]\s*", re.I)


def sin_segun(texto: str) -> str:
    """Quita el «Según la nota del lunes, …» del comienzo: Isabelle lo dice con naturalidad."""
    nuevo = RE_SEGUN.sub("", texto.lstrip(), count=1)
    if nuevo != texto.lstrip() and nuevo:
        nuevo = nuevo[0].upper() + nuevo[1:]
    return nuevo or texto


GENERAL = """[TU TAREA]
La pregunta no está en tu memoria. Si es sobre datos de él, su negocio, sus clientes o personas
que conoce, empieza tu respuesta con NO_ANOTADO y dile en una frase que no lo tienes anotado y
que te lo cuente; no inventes nada. Si es una pregunta general y la sabes con seguridad, responde
con lo que sabes, en 1 a 3 frases. Si no la sabes con seguridad o depende de datos recientes,
locales o que cambian (precios, horarios, noticias), responde solo NO_SE."""


async def _anunciar(pregunta: str, channel: str, chat_id: str, pedida: bool = False) -> Respuesta:
    """Aviso corto y aparte antes de buscar en internet; la búsqueda la hace seguir()."""
    if not internet.queda_cupo():
        return Respuesta(internet.texto("tope", n=internet.config()["busquedas_dia"]), "buscar")
    consulta = internet.consulta_limpia(pregunta)
    if not consulta:
        return Respuesta(internet.texto("sin_consulta"), "buscar")
    pendientes.guardar(channel, chat_id, "busqueda", {"consulta": consulta, "pregunta": pregunta}, 10)
    return Respuesta(internet.texto("pedido" if pedida else "aviso"), "buscar", seguir=True)


async def seguir(channel: str, chat_id: str) -> dict:
    """La continuación de un aviso: hace la búsqueda anunciada y responde con fuentes legibles y
    la pregunta «¿lo guardo?» (pendiente en la base, 30 min)."""
    global _en_curso
    datos = pendientes.tomar(channel, chat_id, "busqueda")
    if not datos:
        return Respuesta("", "nada").dict()
    _en_curso += 1
    try:
        r = await internet.buscar(datos["consulta"])
    except ChatUnavailable as exc:
        resp = Respuesta(str(exc), "aviso")
    else:
        if not r["fuentes"] or not r["texto"]:
            resp = Respuesta(internet.texto("sin_fuentes"), "buscar", "internet", llm=r["llm"])
        else:
            pendientes.guardar(channel, chat_id, "web", {"pregunta": datos["pregunta"], "texto": r["texto"],
                                                         "fuentes": r["fuentes"]}, 30)
            resp = Respuesta(f"{internet.mensaje(r)}\n\n{internet.texto('guardar')}", "buscar", "internet", llm=r["llm"])
    finally:
        _en_curso -= 1
    if resp.accion != "aviso":
        resp.texto = persona.dosificar(resp.texto)
    charla.agregar("isabelle", sin_avisos(resp.texto) or resp.texto, chat_id, resp.accion)
    return resp.dict()


RE_GUARDA_SI = re.compile(r"^(si|sip|dale|ok|okey|claro|por favor|guarda|guardalo|guardala|si guardalo|si por favor|si guarda)\b")
RE_GUARDA_NO = re.compile(r"^(no|nop|no gracias|no hace falta|dejalo|mejor no|asi esta bien)\b")


async def _resolver_web(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """Su respuesta a «¿Quiere que lo guarde en su cerebro?». Si no responde eso, no se guarda
    nada y el mensaje sigue normal."""
    d = pendientes.tomar(channel, chat_id, "web")
    if not d:
        return None
    p = plano(texto)
    if len(p.split()) <= 4 and RE_GUARDA_SI.match(p) and not p.startswith("si no"):
        await asyncio.to_thread(inbox.save, internet.nota(d["pregunta"], d).encode("utf-8"), "internet.txt",
                                "text/plain", "internet", kind="text")
        return Respuesta(internet.texto("guardado"), "guardar")
    if len(p.split()) <= 5 and RE_GUARDA_NO.match(p):
        return Respuesta(internet.texto("no_guardado"), "cierre")
    return None


async def _preguntar(consulta: str, texto: str = "", channel: str = "", chat_id: str = "") -> Respuesta:
    r = await ask.ask(consulta, contexto=_ahora())
    if not (r.get("answer") or "").startswith(ask.NO_INFO):
        texto = r["answer"]
        if r.get("tipo") in ("memoria", "sobre") or r.get("llm"):
            texto = sin_segun(filtro.limpiar(texto) or texto)
        return Respuesta(texto + _fuentes(r.get("sources") or []), "preguntar", "memoria", llm=r.get("llm"))
    sin_internet = internet.no_buscar(texto or consulta)
    if channel and not sin_internet and internet.conviene(consulta):  # reciente o local: internet
        return await _anunciar(consulta, channel, chat_id)
    salida, info = await get_client().chat_ex(
        [{"role": "system", "content": f"[QUIÉN ERES]\n{persona.sistema()}\n\n{_ahora()}\n\n{GENERAL}"},
         {"role": "user", "content": consulta}],
        temperature=0.3, max_tokens=TOKENS["charlar"],
    )
    salida = filtro.limpiar(salida)
    if salida.startswith("NO_ANOTADO") or not salida:
        texto = salida.removeprefix("NO_ANOTADO").lstrip(" :.,-\n") or (
            f"No lo tengo anotado, {persona.tratamiento()}. ¿Me lo cuenta?"
        )
        texto = texto[:1].upper() + texto[1:]
        return Respuesta(texto, "preguntar", None, llm=info)
    if salida.startswith("NO_SE"):  # el modelo no lo sabe: internet, salvo que pidió no buscar
        if sin_internet or not channel:
            return Respuesta(internet.texto("no_buscar"), "preguntar", None, llm=info)
        return await _anunciar(consulta, channel, chat_id)
    return Respuesta(f"{persona.frase('sin_memoria')}\n\n{salida}", "preguntar", "general", llm=info)


def _momento(h: int) -> str:
    return "madrugada" if h < 5 else "mañana" if h < 12 else "tarde" if h < 19 else "noche"


def _ahora() -> str:
    """Fecha, día y hora local de cada turno (NEXUS_TZ_OFFSET), en 12 horas."""
    now = clock.now_local()
    man = now + timedelta(days=1)
    return (f"[AHORA]\nHoy es {when.WEEKDAY_NAMES[now.weekday()]} {now.day} de {when.MONTH_NAMES[now.month - 1]} de {now.year}, "
            f"{when.ampm(now)} (hora de Perú, de {_momento(now.hour)}). Mañana es {when.WEEKDAY_NAMES[man.weekday()]} "
            f"{man.day} de {when.MONTH_NAMES[man.month - 1]}. Escribe las horas en 12 horas con a. m. o p. m.")


TAREAS = {
    "guardado": ("Ya guardaste en tu memoria lo que te acaba de contar: confírmaselo con naturalidad. "
                 "Si con lo que sabes ves un riesgo o algo útil, díselo con respeto; si no, no inventes nada."),
    "charlar": "Conversa con naturalidad, con tu personalidad. Si viene al caso, propón una idea o un consejo.",
    "opinar": "Da tu opinión sincera con argumentos y al menos una idea concreta. Si ves un riesgo real, dilo con respeto.",
    "analizar": "Analiza lo que pide: revisa los números, pros, contras y riesgos, y termina con una recomendación clara.",
}
PROFUNDO = ("Texto plano, sin markdown ni títulos (una lista corta con guiones si hace falta), de unos {largo} caracteres. "
            "Di en una frase qué supones (los datos que no te dio) y termina con qué debería verificar él antes de decidir. "
            "No agregues datos que no estén en la conversación o en tu memoria.")
LARGO = {
    "guardado": "De 1 a 2 frases.",
    "charlar": "De 1 a 3 frases.",
    "opinar": "Máximo 5 frases.",
    "analizar": "Máximo 6 frases.",
}


def _sistema(accion: str, fragmentos: list[dict], profundo: bool, detalle: bool = False) -> str:
    bloques = [
        ("QUIÉN ERES", persona.sistema()),
        ("LO QUE SÉ DEL JEFE (lo que él te contó; no es una fuente para citar)",
         charla.ficha_texto() or "Todavía sabes poco de él."),
        ("RESUMEN DE LA CHARLA", charla.resumen() or "Es una conversación reciente."),
    ]
    if fragmentos:
        bloques.append((
            "MI MEMORIA (fragmentos de sus notas y documentos; si usas uno, dilo con naturalidad)",
            "\n\n".join(f"De su {citations.legible(h['filename'], h.get('created_at'), h['content'], citations.location(h.get('meta') or {}))}:\n{h['content'][:800]}"
                       for h in fragmentos),
        ))
    largo = PROFUNDO.format(largo=LARGO_PROFUNDO * 2 if detalle else LARGO_PROFUNDO) if profundo else LARGO[accion]
    texto = "\n\n".join(f"[{t}]\n{c}" for t, c in bloques)
    return (f"{texto}\n\n{_ahora()}\n\n[TU TAREA]\n{TAREAS[accion]} {largo} Responde solo a su último mensaje; "
            "lo que dijiste antes en la conversación salió de tu memoria: no lo corrijas ni lo pongas en duda salvo que él lo pida.")


async def _hablar(accion: str, texto: str, chat_id: str, profundo: bool) -> Respuesta:
    client = get_client()
    hits = relevant(await search(texto, 8, publish=False))[:FRAGMENTOS_MAX] if len(texto.split()) > 3 else []
    previos = charla.recientes(charla.RECIENTES, chat_id)
    mensajes = [
        {"role": "user" if m["rol"] == "jefe" else "assistant", "content": m["texto"][: charla.TEXTO_TURNO_MAX]}
        for m in previos
    ]
    if not mensajes or mensajes[-1]["role"] != "user":
        mensajes.append({"role": "user", "content": texto})
    modelo = client.deep_model if profundo and client.deep_model else None
    pedido = [{"role": "system", "content": _sistema(accion, hits, profundo, bool(RE_DETALLE.search(plano(texto))))}, *mensajes]
    opciones = dict(model=modelo, temperature=TEMPERATURA[accion], razonar=bool(modelo),
                    max_tokens=TOKENS_PROFUNDO if profundo else TOKENS[accion])
    salida, info = await client.chat_ex(pedido, **opciones)
    salida = sin_segun(sin_avisos(filtro.limpiar(salida))) or persona.frase("no_entendi")
    # Ningún nombre de persona inventado («señor Joaquín»): debe salir de su ficha, sus notas o la
    # conversación. Si aparece, se regenera una vez; si insiste, un respaldo fijo.
    fuentes = [charla.ficha_texto(), charla.resumen(), texto, *(h["content"] for h in hits), *(m["texto"] for m in previos)]
    if malos := nombres.inventados(salida, fuentes):
        log.warning("Respuesta con nombre(s) inventado(s) (%d): se regenera", len(malos))
        salida, info = await client.chat_ex(
            [*pedido, {"role": "assistant", "content": salida}, {"role": "user", "content": nombres.instruccion(malos)}], **opciones)
        salida = sin_segun(sin_avisos(filtro.limpiar(salida))) or persona.frase("no_entendi")
        if nombres.inventados(salida, fuentes):
            log.warning("El nombre inventado persiste: respaldo")
            return Respuesta(persona.frase("respaldo_charla" if accion in ("charlar", "guardado") else "respaldo_analisis"),
                             accion, None, llm=info)
    nivel = None
    if hits and accion not in ("charlar", "guardado"):
        respaldo = citations.supporting(salida, hits)
        if respaldo:
            salida += _fuentes(respaldo)
            nivel = "memoria"
    if modelo and modelo != client.model:
        salida = f"{persona.frase('profundo')}\n\n{salida}"
    return Respuesta(salida, accion, nivel, llm=info)


# ---------- turno ----------


def _avisos_del_codigo() -> tuple[str, ...]:
    """Comienzos de las líneas que arma el código (no la IA): el aviso del modelo potente, los de
    «no sale de mi memoria» y las fuentes. Sin el tratamiento ni la puntuación que lo rodea (el
    tratamiento puede no ir)."""
    frases, out = persona.get()["frases"], []
    for k in ("profundo", "sin_memoria"):
        for v in frases[k] if isinstance(frases[k], list) else [frases[k]]:
            pref = v.split("{")[0].strip().rstrip(",;:").strip().lower()
            if len(pref) >= 12:
                out.append(pref)
    return tuple(out) + (FUENTES.lower(), "fuentes:", "fuentes de internet:", "⚠ esto no lo vi en las fuentes")


def sin_avisos(texto: str) -> str:
    """Quita las líneas que arma el código. Se usa para el historial (si quedaran ahí, el modelo
    las imitaría en otras respuestas) y para limpiar a la IA si aun así las copia."""
    avisos = _avisos_del_codigo()
    lineas = [l for l in texto.split("\n") if not l.strip().lower().startswith(avisos)]
    return "\n".join(lineas).strip()


async def conversar(textos: list[str], channel: str, chat_id: str) -> dict:
    """Un turno: uno o varios mensajes seguidos del jefe -> una respuesta."""
    global _en_curso
    textos = [t.strip() for t in textos if t and t.strip()]
    if not textos:
        return Respuesta(persona.frase("no_entendi"), "nada").dict()
    _en_curso += 1
    charla.pausar()
    try:
        previos = charla.recientes(charla.RECIENTES, chat_id)
        anterior = next((m["texto"] for m in reversed(previos) if m["rol"] == "jefe"), None)
        for t in textos:
            charla.agregar("jefe", t, chat_id)
        iniciativa.al_escribir(channel, chat_id, "\n".join(textos))
        r = await _turno(textos, channel, chat_id, previos, anterior)
    except ChatUnavailable as exc:
        r = Respuesta(str(exc), "aviso")
    finally:
        _en_curso -= 1
    if r.accion != "aviso":  # los avisos de OpenRouter salen tal cual
        r.texto = persona.dosificar(r.texto)
    charla.agregar("isabelle", sin_avisos(r.texto) or r.texto, chat_id, r.accion)
    log.info("Turno: %d mensaje(s), acción %s, nivel %s, %d caracteres", len(textos), r.accion, r.nivel, len(r.texto))
    return r.dict()


def _temporal(texto: str) -> bool:
    """Preguntas que core/questions.py responde con reglas (por fecha, lo último, «qué sé de X»)."""
    return bool(questions.RE_LAST.search(texto) or questions.RE_WHAT.search(texto) or questions.RE_ABOUT.match(texto))


async def _turno(textos, channel, chat_id, previos, anterior) -> Respuesta:
    texto = "\n".join(textos)
    if r := await _resolver_hora(texto, channel, chat_id):
        return r
    if r := iniciativa.resolver(texto):  # «¿prefiere que le escriba menos?»
        return Respuesta(r, "iniciativa")
    if r := await _resolver_web(texto, channel, chat_id):  # «¿lo guardo en su cerebro?»
        return r
    if r := olvido.resolver(texto, channel, chat_id):  # lista de candidatos o «¿confirma que olvide…?»
        return Respuesta(r, "olvido")
    if r := await _resolver_paginas(texto, channel, chat_id):  # «¿sigo con las otras páginas?»
        return r
    if r := await _resolver_medio(texto, channel, chat_id):  # «¿cómo se llama?» -> «se llama Max»
        return r
    if e := _espera(channel, chat_id):
        if r := await _resolver_espera(e, texto, channel, chat_id):
            return r
    if r := await _resolver_pendiente(texto, channel, chat_id):
        return r
    if r := await _reglas(textos, channel, chat_id, anterior):
        return r
    p = plano(texto)
    if internet.pedida(texto):  # «busca en internet…»: aviso y búsqueda, sin pasar por el cerebro
        return await _anunciar(texto, channel, chat_id, pedida=True)
    if RE_SALUDO.match(p):
        return await _hablar("charlar", texto, chat_id, False)
    if RE_PROFUNDO.search(p):
        return await _hablar("opinar" if "opin" in p else "analizar", texto, chat_id, True)
    if _temporal(texto):  # «¿qué aprendí hoy?», «lo último que te mandé»: las resuelve el código
        return await _preguntar(texto)
    datos = await interpretar(texto, previos)
    if datos is None:
        return Respuesta(persona.frase("no_entendi"), "no_entendi")
    if datos["accion"] == "guardar":  # lo guarda tal cual y lo comenta (un consejo o un riesgo, si ve uno)
        await _guardar(texto, channel)
        r = await _hablar("guardado", texto, chat_id, False)
        r.accion = "guardar"
        return r
    if datos["accion"] == "preguntar":
        return await _preguntar(datos["consulta"] or texto, texto, channel, chat_id)
    return await _hablar(datos["accion"], texto, chat_id, False)


RE_BOTON = re.compile(r"rec:(hecho|posponer|cambiar|cancelar|uno|serie):(\d+)|rec:pos:(\d+):(\w+)|pend:(si|otra)")
ETIQUETAS = {"hecho": "Ya lo hice", "posponer": "Recuérdame", "cambiar": "Cambiar", "cancelar": "Cancelar",
             "uno": "Solo este", "serie": "Toda la serie", "si": "Sí", "otra": "No, otra hora"}


async def boton(dato: str, channel: str, chat_id: str) -> dict:
    """Un botón pulsado. Se valida todo: que el dato tenga la forma esperada, que el recordatorio
    siga abierto y que sea de este chat. El botón ya es la confirmación (no se pregunta otra vez)."""
    m = RE_BOTON.fullmatch(dato or "")
    etiqueta = "?"
    if not m:
        r = Respuesta("Ese botón ya no sirve.", "boton")
    elif m.group(5):  # «Sí» / «No, otra hora» de un recordatorio dudoso
        etiqueta = ETIQUETAS[m.group(5)]
        pendiente = reminders.get_pending(channel, chat_id)
        if not pendiente or pendiente.get("reminder_id"):
            r = Respuesta("Eso ya no estaba pendiente.", "boton")
        elif m.group(5) == "si":
            _limpiar_espera(channel, chat_id)
            saved = reminders.confirm(channel, chat_id)
            r = _listo(saved) if saved else Respuesta("Eso ya no estaba pendiente.", "boton")
        else:
            e = _espera(channel, chat_id) or {}
            reminders.clear_pending(channel, chat_id)
            _esperar(channel, chat_id, tipo="otra_hora", texto=e.get("texto") or f"recuérdame {pendiente['tarea']}")
            r = Respuesta(f"¿A qué hora, {persona.tratamiento()}?", "recordatorio")
    else:
        que = m.group(1) or "pos"
        rid = int(m.group(2) or m.group(3))
        etiqueta = ETIQUETAS.get(que) or OPCIONES_POSPONER.get(m.group(4) or "", ("?",))[0]
        actual = _activo(rid, chat_id)
        if not actual:
            r = Respuesta("Ese recordatorio ya no está activo.", "boton")
        elif que == "hecho":
            reminders.cerrar(rid, "hecho", chat_id, "botón ya lo hice")
            r = Respuesta(persona.frase("recordatorios.hecho", tarea=actual["text"]), "recordatorio")
        elif que in ("posponer", "cambiar"):
            r = _pedir_cuando(channel, chat_id, rid)
        elif que == "pos":
            opcion = OPCIONES_POSPONER.get(m.group(4))
            r = await _posponer(actual, opcion[1], channel, chat_id) if opcion else Respuesta("Ese botón ya no sirve.", "boton")
        elif que == "cancelar":
            r = _cancelar(actual, channel, chat_id, confirmar=False)
        elif que == "uno":
            reminders.cerrar(rid, "cancelado", chat_id, "solo este")
            r = Respuesta(persona.frase("recordatorios.cancelado", tarea=actual["text"]), "recordatorio")
        else:  # serie
            n = len(reminders.cancelar_serie(rid, chat_id))
            r = Respuesta(persona.frase("recordatorios.cancelado", tarea=f"{actual['text']} (toda la serie, {n} pendiente(s))"), "recordatorio")
    r.texto = persona.dosificar(r.texto)
    iniciativa.al_escribir(channel, chat_id)
    charla.agregar("jefe", f"[Pulsó «{etiqueta}»]", chat_id)
    charla.agregar("isabelle", r.texto, chat_id, r.accion)
    return r.dict()


# ---------- archivos: ver y oír (Fase 3) ----------

# Una nota de voz suele empezar llamándola («Isabelle, recuérdame…»): se quita para que las reglas
# la entiendan igual que si él la hubiera escrito
RE_LLAMADO = re.compile(r"^\s*(?:(?:oye|hola|ey|mira|este)[\s,]+)?isab[eé]l(?:le|la|a)?[\s,.:;!¡-]+", re.I)
QUE_ENVIO = {"image": "una foto", "audio": "un audio", "video": "un video", "document": "un archivo", "text": "un archivo"}
ESPERA_MEDIO_MIN = 30


def _evento(event_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT id, kind, filename, raw_path, mime, status, error FROM events WHERE id = ?",
                         (event_id,)).fetchone()
    return dict(r) if r else None


def _escaneado(ev: dict) -> bool:
    """Un PDF con páginas sin texto: se mira al llegar. Los demás documentos los lee el worker."""
    if Path(ev["raw_path"]).suffix.lower() != ".pdf":
        return False
    try:
        return bool(documentos.escaneadas(documentos.textos(Path(ev["raw_path"]))))
    except Unsupported:
        return False


async def archivos(items: list[dict], channel: str, chat_id: str, leyenda: str = "", voz: bool = False,
                   grande: bool = False) -> dict:
    """Uno o varios archivos que acaban de llegar por el chat (un álbum es una sola respuesta).
    items: [{"id", "filename", "kind", "duplicate"}] de /inbox/file. Fotos, audios y PDF escaneados
    se analizan aquí mismo (una sola vez: si el archivo ya había llegado se reutiliza lo guardado)."""
    global _en_curso
    iniciativa.al_escribir(channel, chat_id)
    if grande:  # Telegram no deja bajar más de 20 MB: de ese no hay nada guardado
        charla.agregar("jefe", "[Intentó enviar un archivo de más de 20 MB]", chat_id)
    if not items:
        r = Respuesta(medios.frase("grande"), "archivo")
    else:
        _en_curso += 1
        charla.pausar()
        try:
            previos = charla.recientes(charla.RECIENTES, chat_id)
            anterior = next((m["texto"] for m in reversed(previos) if m["rol"] == "jefe"), None)
            for n, it in enumerate(items):
                extra = f" {leyenda}" if leyenda and n == 0 else ""
                charla.agregar("jefe", f"[Envió {QUE_ENVIO.get(it['kind'], 'un archivo')}: {it['filename']}]{extra}", chat_id)
            r = await _archivos(items, channel, chat_id, leyenda, voz, previos=previos, anterior=anterior)
            if grande:
                r.texto = f"{r.texto}\n\n{medios.frase('grande')}"
        except ChatUnavailable as exc:
            r = Respuesta(str(exc), "aviso")
        finally:
            _en_curso -= 1
    if r.accion != "aviso":
        r.texto = persona.dosificar(r.texto)
    charla.agregar("isabelle", sin_avisos(r.texto) or r.texto, chat_id, r.accion)
    log.info("Archivos: %d, acción %s, %d caracteres", len(items), r.accion, len(r.texto))
    return r.dict()


async def _archivos(items, channel, chat_id, leyenda, voz, previos=None, anterior=None, rever=False) -> Respuesta:
    vistos, otros, partes, uso = [], [], [], None
    for it in items:
        ev = _evento(it["id"])
        if not ev:
            continue
        es_medio = medios.es_medio(ev) and (ev["kind"] != "document" or await asyncio.to_thread(_escaneado, ev))
        if es_medio and not rever:
            ev = await processor.procesar_uno(it["id"]) or ev  # ChatUnavailable sube: queda pendiente
        m = medios.get(it["id"]) if es_medio else None
        if m:
            vistos.append((it, ev, m))
        else:
            otros.append((it, ev, es_medio))
    nuevos = [(it, ev, m) for it, ev, m in vistos if not it.get("duplicate")]
    if vistos and not nuevos and not otros:  # todo repetido: se reutiliza lo guardado, sin IA
        return Respuesta(medios.duplicado(vistos[0][2]), "archivo")
    fotos = [m for _, _, m in nuevos if m["tipo"] in ("foto", "pdf")]
    audios = [(it, m) for it, _, m in nuevos if m["tipo"] == "audio"]
    videos = [m for _, _, m in nuevos if m["tipo"] == "video"]
    if fotos:
        pendiente_pag = next((m for m in fotos if medios.paginas_pendientes(m)), None)
        texto, uso = await medios.comentar(fotos, leyenda, con_datos=not pendiente_pag, preguntar=not pendiente_pag)
        partes.append(texto)
        ids = [m["event_id"] for m in fotos]
        if pendiente_pag:
            faltan = medios.paginas_pendientes(pendiente_pag)
            leidas = len(pendiente_pag["partes"])
            partes.append(medios.frase("paginas", total=leidas + faltan, leidas=leidas, faltan=faltan,
                                       costo=f"{medios.costo_paginas(pendiente_pag, faltan):.3f}".replace(".", ",")))
            pendientes.guardar(channel, chat_id, "paginas", {"id": pendiente_pag["event_id"], "leidas": leidas}, ESPERA_MEDIO_MIN)
        elif "?" in texto:
            pendientes.guardar(channel, chat_id, "medio", {"ids": ids, "cifras": bool(medios.importantes(fotos))}, ESPERA_MEDIO_MIN)
    for it, m in audios:
        r = await _audio(it, m, channel, chat_id, previos or [], anterior, rever)
        partes.append(r.texto)
        uso = r.llm or uso
        if r.botones:  # un recordatorio dictado trae sus botones
            return Respuesta("\n\n".join(partes), r.accion, r.nivel, botones=r.botones, llm=uso)
    if videos:
        partes.append(medios.frase("video"))
    for it, ev, es_medio in otros:
        if ev["status"] == "unsupported":
            partes.append(medios.frase("sin_analizar", motivo=(ev["error"] or "").split(". El original")[0].rstrip(".").lower()))
        elif es_medio:  # falló el análisis: el worker lo reintenta
            partes.append(medios.frase("reintento"))
        elif it.get("duplicate"):
            partes.append(persona.frase("duplicado"))
        else:
            partes.append(persona.frase("archivo", archivo=it["filename"]))
    return Respuesta("\n\n".join(p for p in partes if p) or persona.frase("guardado"), "archivo", llm=uso)


async def _audio(it: dict, m: dict, channel: str, chat_id: str, previos: list, anterior, rever: bool) -> Respuesta:
    """Un audio o nota de voz ya transcrito. Si es corto se atiende como si él lo hubiera escrito
    (guardar, preguntar, recordatorios...); si es largo, ella lo comenta. Si no se entendió, lo dice."""
    recorte = ""
    if (m["duracion_s"] or 0) > (m["transcrito_s"] or 0) + 1:
        recorte = medios.frase("recortado", total=round(m["duracion_s"] / 60), max=round(m["transcrito_s"] / 60))
    if m["estado"] != "analizado" or not m["texto"]:
        return Respuesta("\n\n".join(p for p in (medios.frase("no_escuche"), recorte) if p), "archivo")
    if rever or len(m["texto"]) > medios.config()["audio_como_mensaje_max"]:
        texto, uso = await medios.comentar([m])
        return Respuesta("\n\n".join(p for p in (texto, recorte) if p), "archivo", llm=uso)
    charla.agregar("jefe", f"[Nota de voz] {m['texto']}", chat_id)
    texto = RE_LLAMADO.sub("", m["texto"]) if len(RE_LLAMADO.sub("", m["texto"]).split()) >= 2 else m["texto"]
    marca = _nota_de_voz.set(True)
    try:
        r = await _turno([texto[:1].upper() + texto[1:]], channel, chat_id, previos, anterior)
    finally:
        _nota_de_voz.reset(marca)
    if recorte:
        r.texto = f"{r.texto}\n\n{recorte}"
    return r


async def _rever(p: str, channel: str, chat_id: str) -> Respuesta:
    """«Vuelve a mirar la foto»: el único caso en que se vuelve a mirar un original ya analizado."""
    tipo = next((t for w, t in RE_TIPO_MEDIO.items() if re.search(rf"\b{w}\b", p)), None)
    m = medios.ultimo(tipo) if tipo else medios.ultimo()
    if not m or m["tipo"] == "video":
        return Respuesta(medios.frase("nada_que_rever"), "archivo")
    ev = await processor.procesar_uno(m["event_id"], forzar=True)
    it = {"id": m["event_id"], "filename": (ev or {}).get("filename", ""), "kind": (ev or {}).get("kind", ""), "duplicate": False}
    return await _archivos([it], channel, chat_id, "", False, rever=True)


async def _resolver_medio(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """Su respuesta a la pregunta de Isabelle sobre una foto («¿cómo se llama?» -> «se llama Max») o
    a «leí 450 soles; ¿es correcto?». Queda ligada al medio. Si el mensaje no responde eso, se
    descarta la pregunta y el mensaje sigue normal."""
    d = pendientes.tomar(channel, chat_id, "medio")
    if not d:
        return None
    p = plano(texto)
    if (not p or "?" in texto or "¿" in texto or len(p.split()) > 25 or RE_RECORDATORIO.match(texto) or RE_GUARDAR.match(texto)
            or RE_CIERRE.match(p) or RE_SALUDO.match(p) or RE_DESPEDIDA.search(p) or internet.pedida(texto)
            or olvido.RE_PEDIDO.match(p) or RE_REVER.search(p)):
        return None
    if d.get("cifras"):
        c = confirmacion(texto)
        if c == "si":
            medios.confirmar(d["ids"])
            return Respuesta(medios.frase("confirmado"), "archivo")
        if c == "no" or re.search(r"\d", texto):  # «no, son 480»: la corrección queda junto al original
            await medios.anotar(d["ids"], texto)
            return Respuesta(medios.frase("corregido"), "archivo")
        return None
    await medios.anotar(d["ids"], texto)
    r = await _hablar("guardado", texto, chat_id, False)
    r.accion = "archivo"
    return r


async def _resolver_paginas(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """«¿Sigo con las otras páginas?» (PDF escaneado de más de [medios].pdf_paginas_max páginas)."""
    d = pendientes.tomar(channel, chat_id, "paginas")
    if not d:
        return None
    c = confirmacion(texto)
    if c == "no":
        return Respuesta(medios.frase("paginas_no", leidas=d["leidas"]), "archivo")
    if c != "si":
        return None
    ev = _evento(d["id"])
    if not ev or not medios.get(d["id"]):
        return Respuesta(medios.frase("nada_que_rever"), "archivo")
    n = await medios.mas_paginas(d["id"], Path(ev["raw_path"]))
    await processor.procesar_uno(d["id"], rehacer=True)  # trozos nuevos desde lo guardado, sin volver a mirar
    return Respuesta(medios.frase("paginas_listo", n=n), "archivo")
