"""Conversación con Isabelle en lenguaje natural, sin comandos.

Cada turno (uno o varios mensajes seguidos del jefe, que el canal junta):
1. Se guardan en el historial (core/charla.py) y se pausa la actualización de la memoria.
2. Primero lo que espera respuesta en ese chat: confirmar un borrado, la nueva hora de un
   recordatorio, aclarar una hora dudosa o el "¿lo guardo?" de un recordatorio dudoso.
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
Nunca se registra el contenido de los mensajes: solo la acción, el nivel y los tamaños.
"""

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from core import ask, charla, citations, clock, filtro, inbox, persona, questions, reminders, when
from core.db import connect
from core.search import relevant, search
from providers.openrouter import ChatUnavailable, get_client

log = logging.getLogger("nexus.conversa")

ACCIONES = ("guardar", "preguntar", "analizar", "opinar", "charlar")
ESPERA_MIN = 15  # minutos que vale lo que espera respuesta (confirmar, nueva hora...)
FRAGMENTOS_MAX = 4
MAX_FUENTES = 3
TOKENS = {"guardado": 300, "charlar": 400, "opinar": 600, "analizar": 700}
TOKENS_PROFUNDO = 4000
TEMPERATURA = {"guardado": 0.8, "charlar": 0.8, "opinar": 0.8, "analizar": 0.3}

_en_curso = 0


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


def cuando(due_local: str) -> str:
    """'2026-11-02 16:17' -> 'hoy a las 4:17 p. m.', 'mañana martes 3 a las 9:00 a. m.'"""
    dt = datetime.strptime(due_local, "%Y-%m-%d %H:%M")
    now = clock.now_local()
    hora = when.ampm(dt)
    a_las = f"a la {hora}" if hora.startswith("1:") else f"a las {hora}"
    delta = (dt.date() - now.date()).days
    if delta == 0:
        return f"hoy {a_las}"
    rel = {1: "mañana ", 2: "pasado mañana "}.get(delta, "")
    mes = "" if rel else f" de {when.MONTH_NAMES[dt.month - 1]}"
    return f"{rel}{when.WEEKDAY_NAMES[dt.weekday()]} {dt.day}{mes} {a_las}"


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
    r"|minutos?|horas?|\d{1,2} de|el \d{1,2})\b"
)


def _botones(rid: int) -> list:
    return [[{"texto": "Cambiar", "dato": f"rec:cambiar:{rid}"}, {"texto": "Cancelar", "dato": f"rec:cancelar:{rid}"}]]


def _listo(r: dict, verbo: str = "") -> Respuesta:
    texto = f"Listo, {persona.tratamiento()}: {verbo}{cuando(r['due_local'])}: {r['text']}."
    return Respuesta(texto, "recordatorio", botones=_botones(r["id"]))


async def _recordatorio(texto: str, channel: str, chat_id: str) -> Respuesta:
    try:
        p = await reminders.interpret(texto)
    except reminders.NotUnderstood as exc:
        if isinstance(exc.__cause__, when.Ambiguous):  # hora dudosa: se pregunta y se espera la aclaración
            _esperar(channel, chat_id, tipo="aclarar", texto=texto)
        return Respuesta(f"{exc}", "recordatorio")
    if p.get("aviso") or p.get("metodo") == "llm":  # dudosa: ya pasó hoy o la entendió el modelo
        reminders.set_pending(channel, chat_id, p)
        pregunta = "¿Se lo guardo para mañana?" if p.get("aviso") else "¿Se lo guardo así?"
        aviso = f"{p['aviso']} " if p.get("aviso") else ""
        return Respuesta(
            f"{aviso}Entendí {cuando(p['due_local'])}: {p['tarea']}. {pregunta}", "recordatorio"
        )
    return _listo(reminders.create(p["tarea"], p["due_at"], channel, chat_id))


def _lista_recordatorios(chat_id: str) -> Respuesta:
    items = reminders.list_open(chat_id)
    if not items:
        return Respuesta(f"No tiene recordatorios pendientes, {persona.tratamiento()}.", "recordatorios")
    lineas = [f"Sus recordatorios, {persona.tratamiento()}:"]
    lineas += [f"• n.º {r['id']} — {cuando(r['due_local'])}: {r['text']}" for r in items]
    return Respuesta("\n".join(lineas), "recordatorios")


async def _resolver_espera(e: dict, texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """Respuesta a lo que se esperaba, o None si el mensaje es otra cosa (se sigue normal)."""
    tipo = e.get("tipo")
    if tipo == "borrar":
        c = confirmacion(texto)
        if c == "duda":
            _limpiar_espera(channel, chat_id)
            return None
        _limpiar_espera(channel, chat_id)
        if c == "no":
            return Respuesta(f"Lo dejo como está, {persona.tratamiento()}.", "recordatorio")
        r = reminders.set_status(int(e["id"]), "cancelled", chat_id)
        if not r:
            return Respuesta("Ese recordatorio ya no estaba activo.", "recordatorio")
        return Respuesta(f"Cancelado, {persona.tratamiento()}: {r['text']}.", "recordatorio")
    if tipo == "cambiar":
        if confirmacion(texto) == "no":
            _limpiar_espera(channel, chat_id)
            return Respuesta(f"Lo dejo como estaba, {persona.tratamiento()}.", "recordatorio")
        r = reminders.get(int(e["id"]))
        if not r or r["status"] not in ("pending", "sent"):
            _limpiar_espera(channel, chat_id)
            return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
        try:
            p = await reminders.interpret(f"recuérdame {texto} {r['text']}")
        except reminders.NotUnderstood as exc:
            return Respuesta(f"{exc}", "recordatorio")  # se sigue esperando la hora
        _limpiar_espera(channel, chat_id)
        due_at = p["due_at"]
        if not RE_DIA.search(plano(texto)):  # «mejor a las 10»: cambia la hora, no el día
            antes = datetime.strptime(r["due_local"], "%Y-%m-%d %H:%M")
            nueva = datetime.strptime(p["due_local"], "%Y-%m-%d %H:%M")
            mismo_dia = antes.replace(hour=nueva.hour, minute=nueva.minute)
            if mismo_dia > clock.now_local():
                due_at = clock.local_to_utc(mismo_dia).isoformat()
        nuevo = reminders.reschedule(r["id"], due_at, chat_id)
        if not nuevo:
            return Respuesta("Ese recordatorio ya no está activo.", "recordatorio")
        return _listo(nuevo, "lo pasé a ")
    if tipo == "aclarar":
        _limpiar_espera(channel, chat_id)
        if confirmacion(texto) == "no":
            return Respuesta(f"De acuerdo, {persona.tratamiento()}, no lo guardo.", "recordatorio")
        return await _recordatorio(aclarar(e["texto"], texto), channel, chat_id)
    _limpiar_espera(channel, chat_id)
    return None


async def _resolver_pendiente(texto: str, channel: str, chat_id: str) -> Respuesta | None:
    """El "¿se lo guardo?" de un recordatorio dudoso (pending_confirmations)."""
    p = reminders.get_pending(channel, chat_id)
    if not p or p.get("reminder_id"):
        return None
    c = confirmacion(texto)
    if c == "si":
        saved = reminders.confirm(channel, chat_id)
        return _listo(saved) if saved else None
    if c == "no":
        reminders.clear_pending(channel, chat_id)
        return Respuesta(f"De acuerdo, {persona.tratamiento()}, no lo guardo.", "recordatorio")
    if len(plano(texto).split()) <= 3 and not RE_RECORDATORIO.match(texto):
        return Respuesta(f"¿Se lo guardo, {persona.tratamiento()}? Dígame sí o no.", "recordatorio")
    reminders.clear_pending(channel, chat_id)  # escribió otra cosa: se sigue con eso
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
    if RE_RECORDATORIO.match(texto):
        return await _recordatorio(texto, channel, chat_id)
    if RE_LISTA.search(p):
        return _lista_recordatorios(chat_id)
    if RE_HECHO.match(p):
        r = reminders.set_status(None, "done", chat_id)
        if r:
            return Respuesta(f"¡Bien hecho, {persona.tratamiento()}! Lo marqué como hecho: {r['text']}.", "recordatorio")
        return None
    if m := RE_CANCELAR.search(p):
        if "recordatorio" in p or len(p.split()) <= 4:
            r = reminders.get(int(m.group(2)))
            if not r or r["status"] not in ("pending", "sent") or (r["chat_id"] and r["chat_id"] != chat_id):
                return Respuesta(f"No encuentro un recordatorio activo con el n.º {m.group(2)}, {persona.tratamiento()}.", "recordatorio")
            _esperar(channel, chat_id, tipo="borrar", id=r["id"])
            return Respuesta(f"¿Cancelo el recordatorio n.º {r['id']} ({cuando(r['due_local'])}: {r['text']})?", "recordatorio")
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
    r = await asyncio.to_thread(inbox.save, texto.encode("utf-8"), "nota.txt", "text/plain", channel, kind="text")
    return Respuesta(persona.frase("duplicado" if r.get("duplicate") else "guardado"), "guardar")


def _fuentes(sources: list[dict]) -> str:
    nombres = list(dict.fromkeys(s.get("cita") or s["filename"] for s in sources if s.get("filename")))
    return ("\n\nFuentes: " + ", ".join(nombres[:MAX_FUENTES])) if nombres else ""


GENERAL = """[TU TAREA]
La pregunta no está en tu memoria. Si es sobre datos de él, su negocio, sus clientes o personas
que conoce, empieza tu respuesta con NO_ANOTADO y dile en una frase que no lo tienes anotado y
que te lo cuente; no inventes nada. Si es una pregunta general, responde con lo que sabes, en 1 a
3 frases."""


async def _preguntar(consulta: str) -> Respuesta:
    r = await ask.ask(consulta)
    if r.get("answer") != ask.NO_INFO:
        texto = r["answer"]
        if r.get("tipo") in ("memoria", "sobre") or r.get("llm"):
            texto = filtro.limpiar(texto) or texto
        return Respuesta(texto + _fuentes(r.get("sources") or []), "preguntar", "memoria", llm=r.get("llm"))
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
        return Respuesta(texto, "preguntar", None, llm=info)
    return Respuesta(f"{persona.frase('sin_memoria')}\n\n{salida}", "preguntar", "general", llm=info)


def _ahora() -> str:
    now = clock.now_local()
    return f"[AHORA]\nHoy es {when.WEEKDAY_NAMES[now.weekday()]} {now.day} de {when.MONTH_NAMES[now.month - 1]} de {now.year}, {when.ampm(now)} (Perú)."


TAREAS = {
    "guardado": ("Ya guardaste en tu memoria lo que te acaba de contar: confírmaselo con naturalidad. "
                 "Si con lo que sabes ves un riesgo o algo útil, díselo con respeto; si no, no inventes nada."),
    "charlar": "Conversa con naturalidad, con tu personalidad. Si viene al caso, propón una idea o un consejo.",
    "opinar": "Da tu opinión sincera con argumentos y al menos una idea concreta. Si ves un riesgo real, dilo con respeto.",
    "analizar": "Analiza lo que pide: revisa los números, pros, contras y riesgos, y termina con una recomendación clara.",
}
LARGO = {
    "guardado": "De 1 a 2 frases.",
    "charlar": "De 1 a 3 frases.",
    "opinar": "Máximo 5 frases.",
    "analizar": "Máximo 6 frases.",
}


def _sistema(accion: str, fragmentos: list[dict], profundo: bool) -> str:
    bloques = [
        ("QUIÉN ERES", persona.sistema()),
        ("LO QUE SÉ DEL JEFE (lo que él te contó; no es una fuente para citar)",
         charla.ficha_texto() or "Todavía sabes poco de él."),
        ("RESUMEN DE LA CHARLA", charla.resumen() or "Es una conversación reciente."),
    ]
    if fragmentos:
        bloques.append((
            "MI MEMORIA (fragmentos de sus notas y documentos; si usas uno, dilo con naturalidad)",
            "\n\n".join(f"De {citations.cite(h)}:\n{h['content'][:800]}" for h in fragmentos),
        ))
    largo = "Tómate el espacio que necesites; puedes usar una lista corta." if profundo else LARGO[accion]
    texto = "\n\n".join(f"[{t}]\n{c}" for t, c in bloques)
    return f"{texto}\n\n{_ahora()}\n\n[TU TAREA]\n{TAREAS[accion]} {largo}"


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
    salida, info = await client.chat_ex(
        [{"role": "system", "content": _sistema(accion, hits, profundo)}, *mensajes],
        model=modelo, temperature=TEMPERATURA[accion], razonar=bool(modelo),
        max_tokens=TOKENS_PROFUNDO if profundo else TOKENS[accion],
    )
    salida = sin_avisos(filtro.limpiar(salida)) or persona.frase("no_entendi")
    nivel = None
    if hits and accion not in ("charlar", "guardado"):
        respaldo = citations.supporting(salida, hits)
        if respaldo:
            salida += _fuentes([{"cita": citations.cite(h), "filename": h["filename"]} for h in respaldo])
            nivel = "memoria"
    if modelo and modelo != client.model:
        salida = f"{persona.frase('profundo')}\n\n{salida}"
    return Respuesta(salida, accion, nivel, llm=info)


# ---------- turno ----------


def _avisos_del_codigo() -> tuple[str, ...]:
    """Comienzos de las líneas que arma el código (no la IA): el aviso del modelo potente, el de
    «no sale de mi memoria» y las fuentes."""
    frases = persona.get()["frases"]
    return tuple(frases[k].split("{")[0].strip().lower() for k in ("profundo", "sin_memoria")) + ("fuentes:",)


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
        r = await _turno(textos, channel, chat_id, previos, anterior)
    except ChatUnavailable as exc:
        r = Respuesta(str(exc), "aviso")
    finally:
        _en_curso -= 1
    charla.agregar("isabelle", sin_avisos(r.texto) or r.texto, chat_id, r.accion)
    log.info("Turno: %d mensaje(s), acción %s, nivel %s, %d caracteres", len(textos), r.accion, r.nivel, len(r.texto))
    return r.dict()


def _temporal(texto: str) -> bool:
    """Preguntas que core/questions.py responde con reglas (por fecha, lo último, «qué sé de X»)."""
    return bool(questions.RE_LAST.search(texto) or questions.RE_WHAT.search(texto) or questions.RE_ABOUT.match(texto))


async def _turno(textos, channel, chat_id, previos, anterior) -> Respuesta:
    texto = "\n".join(textos)
    if e := _espera(channel, chat_id):
        if r := await _resolver_espera(e, texto, channel, chat_id):
            return r
    if r := await _resolver_pendiente(texto, channel, chat_id):
        return r
    if r := await _reglas(textos, channel, chat_id, anterior):
        return r
    p = plano(texto)
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
        return await _preguntar(datos["consulta"] or texto)
    return await _hablar(datos["accion"], texto, chat_id, False)


async def boton(dato: str, channel: str, chat_id: str) -> dict:
    """Un botón pulsado ("rec:cambiar:12", "rec:cancelar:12")."""
    m = re.fullmatch(r"rec:(cambiar|cancelar):(\d+)", dato or "")
    if not m:
        r = Respuesta("Ese botón ya no sirve.", "boton")
    else:
        que, rid = m.group(1), int(m.group(2))
        actual = reminders.get(rid)
        if not actual or actual["status"] not in ("pending", "sent") or (actual["chat_id"] and actual["chat_id"] != chat_id):
            r = Respuesta("Ese recordatorio ya no está activo.", "boton")
        elif que == "cancelar":  # el botón ya es la confirmación
            reminders.set_status(rid, "cancelled", chat_id)
            r = Respuesta(f"Cancelado, {persona.tratamiento()}: {actual['text']}.", "recordatorio")
        else:
            _esperar(channel, chat_id, tipo="cambiar", id=rid)
            r = Respuesta(f"¿Para cuándo lo paso, {persona.tratamiento()}?", "recordatorio")
    charla.agregar("jefe", f"[Pulsó el botón «{dato.split(':')[1] if ':' in dato else dato}»]", chat_id)
    charla.agregar("isabelle", r.texto, chat_id, r.accion)
    return r.dict()


def archivo(filename: str, kind: str, duplicate: bool, chat_id: str) -> dict:
    """Respuesta al recibir un archivo (comentarlo de verdad llega en la Fase 3)."""
    if duplicate:
        texto = persona.frase("duplicado")
    elif kind == "image":
        texto = persona.frase("foto")
    elif kind == "audio":
        texto = persona.frase("audio")
    else:
        texto = persona.frase("archivo", archivo=filename)
    charla.agregar("jefe", f"[Envió {'una foto' if kind == 'image' else 'un archivo'}: {filename}]", chat_id)
    charla.agregar("isabelle", texto, chat_id, "archivo")
    return Respuesta(texto, "archivo").dict()
