"""Iniciativa propia de Isabelle (Fase 2D): saludo de la mañana y de la noche, sin hostigar.

Horarios y textos en [iniciativa] de profiles/isabelle.toml. Corre en el mismo programador que los
recordatorios (core/worker.py) y sale por el mismo buzón (outbox, kind «saludo»); cada saludo entra
al historial de la charla como mensaje de Isabelle.

Cuándo sale (due_step):
- Mañana a la hora de `manana` y noche a la de `noche`. Si el sistema estuvo apagado, solo hasta
  `manana_hasta` / `noche_hasta`; después se salta. Una fila por día y tipo (UNIQUE): un reinicio
  nunca lo repite.
- Silencio de `silencio_desde` a `silencio_hasta`, salvo una hora que él pidió.
- Si él escribió hace menos de `conversando_min`, espera; si sigue conversando `espera_max_min`
  después de la hora, se salta.
- Un día con saludos y sin ninguna respuesta suya -> desde el día siguiente solo el de la mañana.
  Con `dias_sin_respuesta` días seguidos, en lugar del saludo pregunta si le escribe menos (una
  vez); si tampoco responde, se calla hasta que él escriba. Cualquier mensaje suyo reinicia la cuenta.
- Si se despidió de noche («me voy a dormir», «buenas noches») antes del saludo de la noche, ese
  día no hay saludo de noche.
- Preferencia guardada (kv): normal, menos (solo mañana) o pausa (nada por su cuenta).

Redacción: una llamada al modelo de uso diario (temperatura 0,8) con ficha, resumen de la charla,
agenda de hoy y de mañana y lo real de su día. El código verifica que toda cifra, hora y nombre
propio salga de ese contexto, el largo (1 a 3 frases), que no cuente una «vida propia» y que no
repita el arranque de los últimos 7 saludos; si algo falla, usa una frase de respaldo. La agenda
del día (horas en 12 h, en orden) y los «sin confirmar» de días anteriores (una sola vez) los
escribe el código debajo del saludo.
"""

import json
import logging
import random
import re
import unicodedata
from datetime import date, datetime, time, timedelta

from core import briefing, charla, clock, nombres, outbox, persona, personaje, when
from core.db import connect
from providers.openrouter import get_client

log = logging.getLogger("nexus.iniciativa")

TIPOS = ("manana", "noche")
ABIERTOS = ("creado", "avisado_previo", "avisado", "esperando", "pospuesto")
TEMPERATURA = 0.8
TOKENS = 250
LARGO_MAX = 400
FRASES_MAX = 3
ARRANQUES = 7  # no repetir el arranque de los últimos 7 saludos
REDACTANDO_MIN = 5  # una fila «redactando» más vieja que esto quedó de un apagón: se retoma
PREGUNTA_DIAS = 7  # vida de la pregunta «¿prefiere que le escriba menos?»
K_DESTINO = "iniciativa:destino"
K_PREF = "iniciativa:preferencia"
K_PREGUNTA = "iniciativa:pregunta"
K_MENCIONADOS = "iniciativa:sin_confirmar_mencionados"
PREFERENCIAS = ("normal", "menos", "pausa")
TRATO_PALABRAS = {"jefe", "senor", "josias", "gerente", "ingeniero", "isabelle"}
NUMEROS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
           "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20, "treinta": 30, "cien": 100, "mil": 1000}
PERMITIDOS = {"isabelle", "peru"} | {d for d in when.WEEKDAY_NAMES} | {m for m in when.MONTH_NAMES}
RE_VIDA = re.compile(
    r"\b(fui a|sal[ií] a|sal[ií] con|com[ií]|almorc[eé]|desayun[eé]|cen[eé]|mi familia|mi mam[aá]|mi casa"
    r"|me dorm[ií]|mi d[ií]a fue|estuve en|vi una pel[ií]cula)\b", re.I)
# Suposiciones sobre lo que él hace, siente o piensa («se ve que arrancó temprano», «seguro está cansado»):
# se rechazan si lo que sigue trae palabras que no están en el contexto.
RE_ESPECULA = re.compile(
    r"\b(?:se (?:le )?ve[ií]?a? que|se (?:le )?nota(?:ba)? que|se (?:le )?nota(?:ba)?|se (?:le )?ve[ií]?a?|seguro que|seguro|seguramente"
    r"|debe (?:de )?(?:estar|haber|andar|tener|sentirse)|imagino que|me imagino|se me hace que|parece que|supongo que"
    r"|apuesto a que|ya debe|habr[aá] (?:estado|tenido|sido))\b\s*(?P<resto>[^.,;:!?…]*)", re.I)
VACIAS = {"que", "esta", "estar", "esta", "este", "esto", "eso", "muy", "mas", "bien", "hoy", "ya", "con", "por", "para",
          "una", "uno", "unos", "unas", "los", "las", "del", "sus", "su", "le", "lo", "la", "el", "de", "en", "y", "a",
          "usted", "tiene", "tener", "haber", "sido", "estado", "anda", "andar", "sentirse", "siente", "todo", "toda"}
RE_LISTA = re.compile(r"(^|\n)\s*([-*•#]|\d+[.)])")
RE_HORA = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\ba las? (\d{1,2})(?::(\d{2}))?", re.I)
RE_NOMBRE = re.compile(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+")
RE_DESPEDIDA_NOCHE = re.compile(
    r"\b(me voy a (dormir|descansar|acostar)|ya me (voy a dormir|duermo|acuesto)|a dormir|buenas noches"
    r"|hasta manana|que descanses)\b")
# Ajustes por lenguaje natural (sobre el texto plano: sin tildes ni signos)
RE_LEVANTO = re.compile(r"\b(?:(pasado )?manana)\s+(?:me\s+(?:levanto|despierto)|salud[ae]me|escribeme)\b")
RE_SALUDO_HORA = re.compile(r"\b(saludo|saludos|buenos dias|buenas noches|saludame|escribeme)\b")
RE_SIEMPRE = re.compile(r"\b(desde (hoy|ahora|manana)|de ahora en adelante|siempre|todos los dias|cada dia)\b")
RE_PAUSA = re.compile(r"\b(pausa|pausar|no me escribas|deja de escribirme|no me saludes|deja de saludarme)\b")
RE_MENOS = re.compile(r"\b(menos|escribeme menos|saludame menos|solo (en )?las mananas|solo (los )?buenos dias)\b")
RE_NORMAL = re.compile(r"\b(como siempre|igual que siempre|normal|asi esta bien|esta bien asi|no|nop|para nada"
                       r"|vuelve a saludarme|vuelve a escribirme|saludame de nuevo|saludame otra vez)\b")
RE_SI = re.compile(r"^(si|sip|ya|ok|dale|claro|por favor|si por favor)$")
RE_VUELVE = re.compile(r"\b(vuelve a (saludarme|escribirme)|saludame (de nuevo|otra vez|como siempre)"
                       r"|escribeme como siempre)\b")
RE_PIDE_MENOS = re.compile(r"\b(escribeme menos|saludame menos|solo saludame (en )?las mananas)\b")


# ---------- utilidades ----------


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


def config() -> dict:
    c = persona.get().get("iniciativa")
    if not isinstance(c, dict):
        raise persona.PersonaError(f"{persona.ARCHIVO}: falta la sección [iniciativa]")
    for k in ("manana", "noche", "silencio_desde", "silencio_hasta", "manana_hasta", "noche_hasta"):
        _hhmm(c.get(k), k)
    for k in ("respaldo_manana", "respaldo_noche", "pregunta_menos"):
        if not isinstance(c.get(k), list) or not c[k]:
            raise persona.PersonaError(f"{persona.ARCHIVO}: iniciativa.{k} debe ser una lista de textos")
    return c


def _hhmm(valor, nombre: str = "hora") -> time:
    try:
        h, m = map(int, str(valor).split(":"))
        return time(h, m)
    except (ValueError, TypeError):
        raise persona.PersonaError(f"{persona.ARCHIVO}: iniciativa.{nombre} debe ser HH:MM, no {valor!r}") from None


def _kv(conn, key: str):
    r = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def _set(conn, key: str, value) -> None:
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))


def _del(conn, key: str) -> None:
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))


def _texto(clave: str, **datos) -> str:
    v = config()[clave]
    v = random.choice(v) if isinstance(v, list) else v
    return v.format(trato=persona.tratamiento(), **datos)


def arranque(texto: str) -> str:
    """Las dos primeras palabras del saludo, sin tratamientos: «Buenos días, jefe.» -> «buenos dias»."""
    palabras = [w for w in plano(texto.split("\n\n")[0]).split() if w not in TRATO_PALABRAS]
    return " ".join(palabras[:2])


def _dia_utc(d: date) -> tuple[str, str]:
    ini = clock.local_to_utc(datetime.combine(d, time()))
    return ini.isoformat(), (ini + timedelta(days=1)).isoformat()


def preferencia() -> str:
    with connect() as conn:
        p = _kv(conn, K_PREF)
    return p if p in PREFERENCIAS else "normal"


def hora(dia: date, tipo: str) -> tuple[datetime, bool]:
    """Hora local del saludo ese día y si la pidió él (no aplica el silencio)."""
    with connect() as conn:
        del_dia = _kv(conn, f"iniciativa:hora:{dia.isoformat()}:{tipo}")
        siempre = _kv(conn, f"iniciativa:hora:{tipo}")
    valor = del_dia or siempre or config()[tipo]
    return datetime.combine(dia, _hhmm(valor)), bool(del_dia or siempre)


def _en_silencio(now: datetime) -> bool:
    c = config()
    desde, hasta, t = _hhmm(c["silencio_desde"]), _hhmm(c["silencio_hasta"]), now.time()
    return (t >= desde or t < hasta) if desde > hasta else desde <= t < hasta


# ---------- lo que hace el jefe ----------


def al_escribir(channel: str | None, chat_id: str | None, texto: str = "") -> None:
    """Cada mensaje (o botón) del jefe: guarda a dónde escribirle, da por respondidos los saludos
    enviados (reinicia la cuenta de días sin respuesta) y, si se despidió de noche antes del saludo
    de la noche, ese día ya no lo hay."""
    now = clock.now_local()
    with connect() as conn:
        if channel:
            _set(conn, K_DESTINO, {"channel": channel, "chat_id": chat_id})
        conn.execute("UPDATE iniciativa SET respondido = 1, actualizado_at = ? WHERE estado = 'enviado' AND respondido = 0",
                     (clock.now_utc().isoformat(),))
    if texto and now.hour >= 12 and RE_DESPEDIDA_NOCHE.search(plano(texto)):
        noche, _ = hora(now.date(), "noche")
        if now < noche:
            _saltar(now.date(), "noche", "despedida", noche)


def _destino() -> dict | None:
    with connect() as conn:
        return _kv(conn, K_DESTINO)


def _ultimo_del_jefe() -> datetime | None:
    with connect() as conn:
        r = conn.execute("SELECT created_at FROM charla WHERE rol = 'jefe' ORDER BY id DESC LIMIT 1").fetchone()
    return clock.utc_to_local(r["created_at"]) if r else None


def resolver(texto: str) -> str | None:
    """Su respuesta a «¿prefiere que le escriba menos?». Si su mensaje no responde eso, la pregunta
    se descarta y el mensaje sigue como uno normal (devuelve None): nunca se traga un mensaje."""
    with connect() as conn:
        p = _kv(conn, K_PREGUNTA)
        if not p:
            return None
        _del(conn, K_PREGUNTA)
    if p.get("expira", "") <= clock.now_utc().isoformat():
        return None
    t = plano(texto)
    if not t or len(t.split()) > 8:
        return None
    if RE_PAUSA.search(t):
        elegida = "pausa"
    elif RE_MENOS.search(t) or RE_SI.match(t):
        elegida = "menos"
    elif RE_NORMAL.search(t):
        elegida = "normal"
    else:
        return None
    _guardar_pref(elegida)
    return _texto(elegida)


def _guardar_pref(p: str) -> None:
    with connect() as conn:
        _set(conn, K_PREF, p)
    log.info("Preferencia de saludos: %s", p)


def ajuste(texto: str) -> str | None:
    """Reglas baratas para cambiar los saludos por lenguaje natural. Devuelve la respuesta o None."""
    t = plano(texto)
    if re.search(r"\b(recuerdame|recordarme|avisame|recordatorio)\b", t):  # un recordatorio, no los saludos
        return None
    if RE_VUELVE.search(t):
        _guardar_pref("normal")
        return _texto("normal")
    if RE_PIDE_MENOS.search(t):
        _guardar_pref("menos")
        return _texto("menos")
    levanto = RE_LEVANTO.search(t)
    if not levanto and not RE_SALUDO_HORA.search(t):
        return None
    try:
        reloj, _ = when._parse_time(texto.lower())
    except when.Ambiguous:
        return None
    if not reloj:
        return None
    tipo = "noche" if not levanto and re.search(r"\b(noche|buenas noches)\b", t) else "manana"
    h = reloj.hour
    if not reloj.exact:  # al levantarse o de mañana: a. m.; de noche: p. m.
        h = (h % 12 + 12) if tipo == "noche" else (h % 12 if h != 12 else 12)
    nueva = time(h, reloj.minute)
    hoy = clock.now_local().date()
    with connect() as conn:
        if levanto or (when.RE_MANANA.search(texto) and not RE_SIEMPRE.search(t)):
            dia = hoy + timedelta(days=2 if levanto and levanto.group(1) else 1)
            _set(conn, f"iniciativa:hora:{dia.isoformat()}:{tipo}", nueva.strftime("%H:%M"))
            cuando = "pasado mañana" if dia - hoy == timedelta(days=2) else "mañana"
            log.info("Saludo de %s del %s a las %s", tipo, dia, nueva)
            return _texto("ajuste_dia", cuando=cuando, hora=when.ampm(nueva))
        _set(conn, f"iniciativa:hora:{tipo}", nueva.strftime("%H:%M"))
    log.info("Saludo de %s desde ahora a las %s", tipo, nueva)
    return _texto("ajuste_siempre", saludo="de la mañana" if tipo == "manana" else "de la noche", hora=when.ampm(nueva))


# ---------- filas ----------


def _fila(dia: date, tipo: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM iniciativa WHERE dia = ? AND tipo = ?", (dia.isoformat(), tipo)).fetchone()
    return dict(r) if r else None


def _saltar(dia: date, tipo: str, motivo: str, programada: datetime | None = None) -> bool:
    now = clock.now_utc().isoformat()
    with connect() as conn:
        n = conn.execute(
            "INSERT OR IGNORE INTO iniciativa (dia, tipo, estado, motivo, hora, creado_at, actualizado_at)"
            " VALUES (?, ?, 'saltado', ?, ?, ?, ?)",
            (dia.isoformat(), tipo, motivo, programada.strftime("%H:%M") if programada else None, now, now),
        ).rowcount
    if n:
        log.info("Saludo de %s del %s saltado (%s)", tipo, dia, motivo)
    return bool(n)


def dias_sin_respuesta(hoy: date) -> int:
    """Días seguidos (antes de hoy) con saludos enviados y ninguno respondido. Los días sin
    saludos enviados no cortan ni suman."""
    desde = (hoy - timedelta(days=14)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            "SELECT dia, MAX(respondido) AS r FROM iniciativa WHERE estado = 'enviado' AND dia < ? AND dia >= ?"
            " GROUP BY dia ORDER BY dia DESC", (hoy.isoformat(), desde)).fetchall()
    n = 0
    for r in rows:
        if r["r"]:
            break
        n += 1
    return n


def _ya_pregunto() -> bool:
    """Ya preguntó «¿le escribo menos?» después de su última respuesta."""
    with connect() as conn:
        r = conn.execute("SELECT 1 FROM iniciativa WHERE fuente = 'pregunta' AND respondido = 0 LIMIT 1").fetchone()
    return bool(r)


# ---------- contexto ----------


def _recordatorios(dia: date) -> list[dict]:
    ini, fin = _dia_utc(dia)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, due_at, text FROM reminders WHERE estado IN ({','.join('?' * len(ABIERTOS))})"
            " AND due_at >= ? AND due_at < ? ORDER BY due_at", (*ABIERTOS, ini, fin)).fetchall()
    out = []
    for r in rows:
        local = clock.utc_to_local(r["due_at"])
        out.append({"id": r["id"], "dt": local, "hora": when.ampm(local), "texto": r["text"]})
    return out


def _cerrados_hoy(dia: date) -> list[str]:
    ini, fin = _dia_utc(dia)
    with connect() as conn:
        rows = conn.execute("SELECT text FROM reminders WHERE estado = 'hecho' AND done_at >= ? AND done_at < ?"
                            " ORDER BY done_at", (ini, fin)).fetchall()
    return [r["text"] for r in rows]


def _sin_confirmar(dia: date) -> list[dict]:
    """Sin confirmar de días anteriores que todavía no mencionó."""
    ini, _ = _dia_utc(dia)
    with connect() as conn:
        ya = set(_kv(conn, K_MENCIONADOS) or [])
        rows = conn.execute("SELECT id, due_at, text FROM reminders WHERE estado = 'sin_confirmar' AND due_at < ?"
                            " ORDER BY due_at", (ini,)).fetchall()
    out = []
    for r in rows:
        if r["id"] in ya:
            continue
        d = clock.utc_to_local(r["due_at"])
        out.append({"id": r["id"], "texto": r["text"], "cuando": f"{when.WEEKDAY_NAMES[d.weekday()]} {d.day}"})
    return out


def _agenda(items: list[dict]) -> str:
    return "\n".join(f"• {r['hora']}: {r['texto']}" for r in items)


def contexto(tipo: str, now: datetime) -> dict:
    """Todo lo que la IA puede usar (y nada más): también es la base de la verificación."""
    from core import conversa  # evita la importación circular (conversa usa este módulo)

    hoy = now.date()
    agenda_hoy = [r for r in _recordatorios(hoy) if tipo == "manana" or r["dt"] >= now]  # de noche, lo que falta
    manana = _recordatorios(hoy + timedelta(days=1))
    bloques = [
        ("LO QUE SÉ DEL JEFE", charla.ficha_texto() or "Todavía sabes poco de él."),
        ("RESUMEN DE LA CHARLA", charla.resumen() or "Sin conversación reciente."),
        ("AGENDA DE HOY", (f"{len(agenda_hoy)} recordatorio(s):\n" + _agenda(agenda_hoy)) if agenda_hoy else "Nada programado."),
        ("AGENDA DE MAÑANA", (f"{len(manana)} recordatorio(s):\n" + _agenda(manana)) if manana else "Nada programado."),
    ]
    if tipo == "noche":
        notas = briefing.learned(hoy, limit=5)
        cerrados = _cerrados_hoy(hoy)
        dia = []
        if notas:
            dia.append(f"Guardó {len(notas)} nota(s) o archivo(s) hoy: " + "; ".join(
                (n["resumen"] or n["archivo"] or "")[:120] for n in notas))
        if cerrados:
            dia.append(f"Cerró {len(cerrados)} recordatorio(s) hoy: " + "; ".join(cerrados))
        bloques.append(("SU DÍA (lo real; si está vacío, no inventes nada)", "\n".join(dia) or "Nada registrado hoy."))
    texto = "\n\n".join(f"[{t}]\n{c}" for t, c in bloques)
    return {"texto": texto, "ahora": conversa._ahora(), "agenda_hoy": agenda_hoy, "manana": manana}


# ---------- verificación ----------


def verificar(texto: str, tipo: str, ctx: str, previos: list[str]) -> str | None:
    """None si el saludo sirve; si no, el motivo. Toda cifra, hora y nombre propio debe estar en el
    contexto; de 1 a 3 frases; sin listas, sin «vida propia» y sin repetir un arranque reciente."""
    t = " ".join(texto.split())
    if not t:
        return "vacío"
    frases = re.findall(r"[.!?…]+(?=\s|$)", re.sub(r"\b([ap])\. ?m\.", r"\1m", t))
    if len(t) > LARGO_MAX or len(frases) > FRASES_MAX or RE_LISTA.search(texto):
        return "largo o con lista"
    preguntas = t.count("?")
    if tipo == "noche" and preguntas != 1 or tipo == "manana" and preguntas > 1:
        return "preguntas"
    if RE_VIDA.search(t):
        return "vida propia"
    base = plano(ctx)
    palabras = set(base.split())
    for m in RE_ESPECULA.finditer(t):
        nuevas = [w for w in plano(m.group("resto")).split() if w not in VACIAS and w not in palabras]
        if nuevas or not m.group("resto").strip():
            return f"supone ({' '.join(plano(m.group(0)).split()[:6])})"
    cifras = set(re.findall(r"\d+", ctx))
    horas = {(int(m[0] or m[3]), int(m[1] or m[4] or 0)) for m in RE_HORA.findall(ctx)}
    for m in RE_HORA.findall(t):  # una hora debe estar en el contexto (no basta que el número aparezca)
        if (int(m[0] or m[3]), int(m[1] or m[4] or 0)) not in horas:
            return f"hora {m[0] or m[3]}"
    for n in re.findall(r"\d+", t):
        if n not in cifras:
            return f"cifra {n}"
    for w in plano(t).split():
        if w in NUMEROS and str(NUMEROS[w]) not in cifras and w not in palabras:
            return f"cifra {w}"
    if malos := nombres.inventados(t, [ctx]):  # «Buenos días, señor Joaquín»: nombre de persona inventado
        return f"nombre {malos[0]}"
    if rotos := personaje.problemas(t):  # «soy una IA», «estoy llegando a la oficina»
        return rotos[0]
    for m in RE_NOMBRE.finditer(t):
        antes = t[: m.start()].rstrip()
        if not antes or antes[-1] in ".!?¿¡:\"«":
            continue  # inicio de frase: no se distingue de un nombre
        w = plano(m.group(0))
        if w not in TRATO_PALABRAS and w not in PERMITIDOS and w not in palabras:
            return f"nombre {m.group(0)}"
    if arranque(t) in {arranque(p) for p in previos}:
        return f"arranque repetido ({arranque(t)})"
    return None


# ---------- redactar ----------

TAREA = {
    "manana": ("Escribe tu saludo de buenos días para el jefe: de 1 a 3 frases, cálido y natural, con cariño medio, "
               "como una persona real por WhatsApp. {agenda} No inventes ningún hecho. No supongas ni afirmes qué está haciendo, sintiendo o pensando él (nada de «se ve que», «seguro que», «debe estar», «imagino que», «parece que»): si quieres saberlo, pregúntaselo. Usa solo lo que está arriba. "
               "Como mucho una pregunta. Sin listas ni horas: la agenda la pongo yo debajo."),
    "noche": ("Escribe tu saludo de buenas noches: pregúntale cómo le fue, como una amiga, de 1 a 3 frases con UNA sola "
              "pregunta en todo el mensaje (un solo signo «?»). Si en [SU DÍA] o en la agenda de mañana hay algo real, puedes mencionar UNA cosa; si no, no "
              "inventes nada. No supongas ni afirmes cómo está, qué hizo o qué siente él si no está arriba (nada de «se ve que», "
              "«seguro que», «debe estar», «imagino que», «parece que»): pregúntaselo. No cuentes nada de tu propia vida "
              "(no comes, no sales, no tienes familia)."),
}


def _previos() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT texto FROM iniciativa WHERE estado = 'enviado' AND texto IS NOT NULL"
                            " ORDER BY enviado_at DESC LIMIT ?", (ARRANQUES,)).fetchall()
    return [r["texto"] for r in rows]


def _respaldo(tipo: str, previos: list[str]) -> str:
    usados = {arranque(p) for p in previos}
    opciones = config()[f"respaldo_{tipo}"]
    libres = [o for o in opciones if arranque(o.format(trato="jefe")) not in usados] or opciones
    return random.choice(libres).format(trato=persona.tratamiento())


async def redactar(tipo: str, now: datetime) -> dict:
    """El saludo (sin la agenda): {texto, fuente, detalle, ctx}. Una llamada a la IA; otra solo si
    inventó un nombre de persona."""
    ctx = contexto(tipo, now)
    previos = _previos()
    agenda = (f"Hoy tiene {len(ctx['agenda_hoy'])} recordatorio(s); puedes aludir a cómo pinta el día."
              if ctx["agenda_hoy"] else "Hoy no tiene recordatorios.")
    evitar = sorted({arranque(p) for p in previos} - {""})
    tarea = TAREA[tipo].format(agenda=agenda) + " No menciones la hora actual." + (
        f"\nPROHIBIDO empezar con estas palabras (ya las usaste en los últimos saludos): "
        f"{', '.join('«' + a + '»' for a in evitar)}. Empieza distinto: con algo del día, una pregunta o una frase cariñosa."
        if evitar else "")
    sistema = (f"[QUIÉN ERES]\n{persona.sistema()}\n\n{ctx['texto']}\n\n{ctx['ahora']}\n\n[TU TAREA]\n{tarea}")
    permitido = "\n".join((ctx["texto"], ctx["ahora"], " ".join(persona.get()["trato"]["tratamientos"])))
    detalle = None
    try:
        pedido = [{"role": "system", "content": sistema},
                  {"role": "user", "content": "(Es la hora de tu saludo. Escríbelo ahora, solo el mensaje.)"}]
        salida, _ = await get_client().chat_ex(pedido, temperature=TEMPERATURA, max_tokens=TOKENS)
        salida = (salida or "").strip().strip('"«»').strip()
        detalle = verificar(salida, tipo, permitido, previos)
        if detalle and (malos := nombres.inventados(salida, [permitido])):  # un nombre inventado: otra vez
            salida, _ = await get_client().chat_ex(
                [*pedido, {"role": "assistant", "content": salida}, {"role": "user", "content": nombres.instruccion(malos)}],
                temperature=TEMPERATURA, max_tokens=TOKENS)
            salida = (salida or "").strip().strip('"«»').strip()
            detalle = verificar(salida, tipo, permitido, previos)
    except Exception as exc:  # IA caída, sin saldo, tope diario...
        salida, detalle = "", f"IA: {type(exc).__name__}"
    if detalle:
        log.info("Saludo de %s con respaldo (%s)", tipo, detalle)
        return {"texto": _respaldo(tipo, previos), "fuente": "respaldo", "detalle": detalle, "ctx": ctx}
    return {"texto": salida, "fuente": "ia", "detalle": None, "ctx": ctx}


def _lista(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


async def _enviar(dia: date, tipo: str, programada: datetime, now: datetime, dias: int, destino: dict) -> str:
    now_utc = clock.now_utc()
    viejo = (now_utc - timedelta(minutes=REDACTANDO_MIN)).isoformat()
    with connect() as conn:  # se toma la fila: si otro proceso la tiene, nada
        n = conn.execute(
            "INSERT INTO iniciativa (dia, tipo, estado, hora, dias_sin_respuesta, creado_at, actualizado_at)"
            " VALUES (?, ?, 'redactando', ?, ?, ?, ?) ON CONFLICT(dia, tipo) DO UPDATE SET actualizado_at = excluded.actualizado_at"
            " WHERE iniciativa.estado = 'redactando' AND iniciativa.actualizado_at < ?",
            (dia.isoformat(), tipo, programada.strftime("%H:%M"), dias, now_utc.isoformat(), now_utc.isoformat(), viejo),
        ).rowcount
    if not n:
        return ""
    sin_conf: list[dict] = []
    if dias >= config()["dias_sin_respuesta"]:
        texto, fuente, detalle = _texto("pregunta_menos"), "pregunta", None
    else:
        r = await redactar(tipo, now)
        texto, fuente, detalle = r["texto"], r["fuente"], r["detalle"]
        partes = [texto]
        if tipo == "manana" and r["ctx"]["agenda_hoy"]:
            partes.append(config()["agenda"] + "\n" + _agenda(r["ctx"]["agenda_hoy"]))
        sin_conf = _sin_confirmar(dia)
        if sin_conf:
            partes.append(config()["sin_confirmar"].format(
                lista=_lista([f"«{s['texto']}» ({s['cuando']})" for s in sin_conf])))
        texto = "\n\n".join(partes)
    texto = persona.dosificar(texto)
    with connect() as conn:
        n = conn.execute(
            "UPDATE iniciativa SET estado = 'enviado', texto = ?, fuente = ?, detalle = ?, enviado_at = ?, actualizado_at = ?"
            " WHERE dia = ? AND tipo = ? AND estado = 'redactando'",
            (texto, fuente, detalle, clock.now_utc().isoformat(), clock.now_utc().isoformat(), dia.isoformat(), tipo),
        ).rowcount
        if not n:
            return ""
        outbox.put(conn, destino["channel"], destino.get("chat_id"), "saludo", f"{dia.isoformat()}:{tipo}", texto)
        conn.execute("INSERT INTO charla (created_at, chat_id, rol, texto, accion) VALUES (?, ?, 'isabelle', ?, 'saludo')",
                     (clock.now_utc().isoformat(), destino.get("chat_id"), texto))
        if sin_conf:
            _set(conn, K_MENCIONADOS, sorted(set(_kv(conn, K_MENCIONADOS) or []) | {s["id"] for s in sin_conf}))
        if fuente == "pregunta":
            _set(conn, K_PREGUNTA, {"dia": dia.isoformat(),
                                    "expira": (clock.now_utc() + timedelta(days=PREGUNTA_DIAS)).isoformat()})
    log.info("Saludo de %s del %s en el buzón (%s)", tipo, dia, fuente)
    return fuente


# ---------- programador ----------


async def due_step() -> dict:
    """Paso del programador: decide y envía (o salta) los saludos que tocan ahora."""
    from core import conversa

    now = clock.now_local()
    hoy = now.date()
    destino = _destino()
    if not destino:
        return {}  # todavía no le escribió nunca: no sabe a dónde
    c = config()
    hecho = {}
    for tipo in TIPOS:
        fila = _fila(hoy, tipo)
        if fila and fila["estado"] != "redactando":
            continue
        programada, pedida = hora(hoy, tipo)
        if now < programada:
            continue
        limite = max(datetime.combine(hoy, _hhmm(c[f"{tipo}_hasta"])), programada + timedelta(hours=1))
        if now >= limite:
            _saltar(hoy, tipo, "tarde", programada)
            continue
        if not pedida and _en_silencio(now):
            continue
        pref = preferencia()
        dias = dias_sin_respuesta(hoy)
        if pref == "pausa":
            _saltar(hoy, tipo, "pausa", programada)
            continue
        if tipo == "noche" and pref == "menos":
            _saltar(hoy, tipo, "preferencia", programada)
            continue
        if tipo == "noche" and dias >= 1:
            _saltar(hoy, tipo, "sin_respuesta", programada)
            continue
        if dias >= c["dias_sin_respuesta"] and _ya_pregunto():
            _saltar(hoy, tipo, "sin_respuesta", programada)
            continue
        ultimo = _ultimo_del_jefe()
        if conversa.ocupado() or (ultimo and now - ultimo < timedelta(minutes=c["conversando_min"])):
            if now >= programada + timedelta(minutes=c["espera_max_min"]):
                _saltar(hoy, tipo, "conversando", programada)
            continue
        if fuente := await _enviar(hoy, tipo, programada, now, dias, destino):
            hecho[tipo] = fuente
    return {"saludos": hecho} if hecho else {}


def estado(dias: int = 14) -> dict:
    """Para GET /iniciativa: preferencia, horas de hoy y los últimos saludos."""
    hoy = clock.now_local().date()
    with connect() as conn:
        rows = conn.execute("SELECT dia, tipo, estado, motivo, hora, texto, fuente, detalle, respondido,"
                            " dias_sin_respuesta FROM iniciativa WHERE dia >= ? ORDER BY dia DESC, tipo",
                            ((hoy - timedelta(days=dias)).isoformat(),)).fetchall()
        destino = _kv(conn, K_DESTINO)
        pregunta = _kv(conn, K_PREGUNTA)
    return {
        "preferencia": preferencia(),
        "horas_hoy": {t: hora(hoy, t)[0].strftime("%H:%M") for t in TIPOS},
        "canal": (destino or {}).get("channel"),
        "pregunta_pendiente": bool(pregunta),
        "dias_sin_respuesta": dias_sin_respuesta(hoy),
        "saludos": [dict(r) for r in rows],
    }
