"""Recordatorios con seguimiento hasta que el jefe confirme. El programador no usa IA.

Estados (columna estado; la columna status quedó de versiones anteriores y ya no se usa):
  creado         guardado; proximo_envio = aviso previo (30 min antes) o la hora exacta
  avisado_previo salió el aviso previo; proximo_envio = la hora exacta
  avisado        salió el aviso a la hora (botones «Ya lo hice» y «Recuérdame»)
  esperando      salió al menos una insistencia (cada REMINDER_RETRY_MIN, 20 min)
  pospuesto      el jefe pidió otra hora: vuelve a empezar como creado
  hecho          «ya lo hice» (cierra solo esta ocurrencia)
  cancelado      cancelado (esta ocurrencia o toda la serie)
  sin_confirmar  salió la 3.ª insistencia sin respuesta: se deja de insistir (la Fase 2D lo
                 menciona en el siguiente saludo); «ya lo hice» aún lo cierra
El aviso previo solo existe si se creó con más de 30 min de anticipación. Los avisos programados
suenan a su hora aunque sea de noche (el silencio es solo para saludos e iniciativa propia).

Sin duplicados ni pérdidas: cada aviso y su cambio de estado se escriben en la misma transacción,
y solo si proximo_envio sigue siendo el leído (otro proceso o un reinicio no lo repiten). Si el
sistema estuvo caído, lo vencido sale con «(retrasado)» y las insistencias siguen espaciadas.

Hora ambigua (de 1 a 12 sin am/pm ni contexto): no se adivina. interpret() lanza HoraAmbigua y
la conversación pregunta por texto («¿Las 7 de la mañana o de la noche?»); la pregunta queda en
pending_confirmations (kind 'hora', caduca en PREGUNTA_HORA_MIN). Excepción: si es de una sola
vez y la próxima ocurrencia cae en menos de 2 horas, se toma. Los que se repiten siempre preguntan.

Repetición (regla JSON: diaria, semanal o mensual): cada ocurrencia es una fila de la misma serie
(serie_id = id de la primera). Al cerrarse una (hecha, sin confirmar o cancelada «solo este») se
crea la siguiente.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from config import settings
from core import clock, events, outbox, persona, when
from core.db import connect
from providers.openrouter import get_client

log = logging.getLogger("nexus.reminders")

ABIERTOS = ("creado", "avisado_previo", "avisado", "esperando", "pospuesto")
CERRADOS = ("hecho", "cancelado", "sin_confirmar")
PREVIO_MIN = 30  # aviso previo: 30 min antes, si se creó con más anticipación
INSISTENCIAS = 3
RETRASO_MIN = 2  # más tarde que esto respecto de lo programado: el aviso dice «(retrasado)»
ATRASO_S = 120  # un aviso vencido hace más que esto sin salir al buzón: el vigilante lo reenvía
AMBIGUA_TOMA_H = 2  # hora sin am/pm de una sola vez: si la próxima cae antes de esto, se toma
PREGUNTA_HORA_MIN = 30  # vida de la pregunta «¿de la mañana o de la noche?»
ESTADO_ES = {
    "creado": "programado", "pospuesto": "pospuesto", "avisado_previo": "programado (ya le avisé antes)",
    "avisado": "esperando su confirmación", "esperando": "esperando su confirmación",
    "sin_confirmar": "sin confirmar", "hecho": "hecho", "cancelado": "cancelado",
}

LLM_PROMPT = (
    "Hoy es {hoy} y son las {hora}. Del siguiente pedido de recordatorio extrae la fecha, "
    "la hora y la tarea. Responde SOLO con JSON así: "
    '{{"fecha": "AAAA-MM-DD", "hora": "HH:MM", "tarea": "..."}}. '
    'Si no hay ninguna fecha u hora, responde {{"fecha": null}}.\n\nPedido: {texto}'
)


class NotUnderstood(ValueError):
    pass


class HoraAmbigua(NotUnderstood):
    """Hora sin am/pm que no se puede deducir: hay que preguntarla. `datos` es lo que se guarda
    para resolverla después: fecha, h, m, con_dia, tarea y regla."""

    def __init__(self, datos: dict):
        super().__init__(f"¿{when.pregunta_ampm(datos['h'], datos['m'])}?")
        self.datos = datos


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


# ---------- interpretar ----------


def _result(
    dt_local: datetime, task: str, method: str, now: datetime, passed_today: bool = False,
    regla: dict | None = None,
) -> dict:
    if dt_local <= now:
        raise NotUnderstood(
            f"La fecha entendida ({when.describe(dt_local, now)}) ya pasó. Dígamela de nuevo."
        )
    if not task:
        raise NotUnderstood("Entendí la fecha, pero no qué debo recordarle.")
    r = {
        "due_at": clock.local_to_utc(dt_local).isoformat(),
        "due_local": dt_local.strftime("%Y-%m-%d %H:%M"),
        "hora": dt_local.strftime("%H:%M"),
        "entendido": when.describe(dt_local, now),
        "tarea": task,
        "metodo": method,
        "regla": regla,
    }
    if passed_today:  # pidió una hora inequívoca que hoy ya pasó: se pregunta por mañana
        r["aviso"] = f"Las {when.ampm(dt_local)} de hoy ya pasaron. ¿Es para mañana?"
    return r


async def _llm_fallback(text: str, now: datetime) -> dict | None:
    prompt = LLM_PROMPT.format(
        hoy=f"{when.WEEKDAY_NAMES[now.weekday()]} {now:%Y-%m-%d}", hora=f"{now:%H:%M}", texto=text
    )
    try:
        raw = await get_client().chat([{"role": "user", "content": prompt}])
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        if not data.get("fecha"):
            return None
        hora = data.get("hora") or "09:00"
        dt = datetime.strptime(f"{data['fecha']} {hora}", "%Y-%m-%d %H:%M")
        task = when._clean_task(str(data.get("tarea") or ""))
        return {"dt": dt, "task": task}
    except Exception as exc:  # salida inválida del modelo: se descarta
        log.info("El LLM no pudo interpretar la fecha (%s)", type(exc).__name__)
        return None


async def interpret(text: str, usar_ia: bool = True) -> dict:
    """No guarda nada: devuelve lo entendido (con su regla de repetición, si la hay). Con
    usar_ia=False solo reglas (posponer, cambiar la hora: nunca gasta tokens)."""
    now = clock.now_local()
    try:
        rep = when.repeticion(text, now)
        p = when.parse(rep.texto if rep else text, now)
    except when.Ambiguous as exc:  # no se adivina la hora (ni con el modelo): se pregunta
        raise NotUnderstood(str(exc)) from exc
    if p and p.ambigua:
        if not p.task:
            raise NotUnderstood("Entendí la fecha, pero no qué debo recordarle.")
        if rep or p.when - now >= timedelta(hours=AMBIGUA_TOMA_H):
            raise HoraAmbigua({**p.ambigua, "tarea": p.task, "regla": rep.regla if rep else None})
    if p and rep:
        dt = p.when
        while dt <= now:  # «todos los lunes a las 8» un lunes a las 10: el próximo lunes
            dt = when.siguiente(rep.regla, dt)
        return _result(dt, p.task, "reglas", now, regla=rep.regla)
    if p:
        return _result(p.when, p.task, "reglas", now, p.passed_today)
    if usar_ia and not rep:
        guess = await _llm_fallback(text, now)
        if guess:
            return _result(guess["dt"], guess["task"], "llm", now)
    raise NotUnderstood(
        "No entendí la fecha. Pruebe así: «recuérdame mañana a las 9 am llamar al proveedor»."
    )


# ---------- guardar ----------


def _primer_envio(created: datetime, due: datetime) -> datetime:
    """Aviso previo 30 min antes solo si hay más de 30 min de anticipación; si no, a la hora."""
    if due - created > timedelta(minutes=PREVIO_MIN):
        return due - timedelta(minutes=PREVIO_MIN)
    return due


def _insert(conn, text: str, due_at: str, channel: str | None, chat_id: str | None,
            source_event_id: str | None = None, regla: dict | None = None,
            serie_id: int | None = None) -> int:
    due = datetime.fromisoformat(due_at)
    if due.tzinfo is None:
        raise ValueError("due_at debe incluir zona horaria (UTC)")
    due = due.astimezone(timezone.utc)
    now = clock.now_utc()
    rid = conn.execute(
        "INSERT INTO reminders (created_at, due_at, text, channel, chat_id, source_event_id,"
        " estado, proximo_envio, insistencias, regla, serie_id, anticipacion_min)"
        " VALUES (?, ?, ?, ?, ?, ?, 'creado', ?, 0, ?, ?, ?)",
        (
            now.isoformat(), due.isoformat(), text, channel or settings.DEFAULT_CHANNEL, chat_id,
            source_event_id, _primer_envio(now, due).isoformat(),
            json.dumps(regla) if regla else None, serie_id, int((due - now).total_seconds() // 60),
        ),
    ).lastrowid
    if regla and serie_id is None:
        conn.execute("UPDATE reminders SET serie_id = ? WHERE id = ?", (rid, rid))
    return rid


def _saved(rid: int) -> dict:
    """Relee la fila escrita: nunca se da por guardado algo que no está en la base."""
    r = get(rid)
    if not r:
        raise RuntimeError(f"el recordatorio {rid} no quedó guardado")
    log.info("Recordatorio %s · creado para %s (hora local), primer aviso %s", rid, r["due_local"], r["proximo_local"])
    events.publish("reminder", {"id": rid})
    return r


def create(
    text: str,
    due_at: str,
    channel: str | None = None,
    chat_id: str | None = None,
    source_event_id: str | None = None,
    regla: dict | None = None,
) -> dict:
    with connect() as conn:
        rid = _insert(conn, text, due_at, channel, chat_id, source_event_id, regla)
    return _saved(rid)


def create_test(channel: str | None, chat_id: str | None, seconds: int = 60) -> dict:
    """Aviso de prueba a los N segundos, para ver el camino entero."""
    due = clock.now_utc() + timedelta(seconds=seconds)
    r = create(f"Prueba de recordatorio ({seconds} s)", due.isoformat(), channel, chat_id)
    log.info("Recordatorio %s · prueba: debe llegar a %s por %s", r["id"], r["due_local"], r["channel"])
    return r


# ---------- confirmación de lo dudoso (pending_confirmations) ----------


def set_pending(channel: str, chat_id: str, parsed: dict) -> dict:
    """Guarda lo entendido a la espera del "sí" (reemplaza cualquier pendiente del chat)."""
    now = clock.now_utc()
    expires = now + timedelta(minutes=settings.REMINDER_CONFIRM_MIN)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_confirmations (channel, chat_id, kind, payload,"
            " created_at, expires_at, reminder_id) VALUES (?, ?, 'reminder', ?, ?, ?, NULL)",
            (channel, chat_id, json.dumps(parsed, ensure_ascii=False), now.isoformat(), expires.isoformat()),
        )
    log.info("Confirmación pendiente en %s (caduca %s)", channel, expires.isoformat(timespec="seconds"))
    return {**parsed, "caduca": expires.isoformat(), "reminder_id": None}


def get_pending(channel: str, chat_id: str) -> dict | None:
    """Lo pendiente de ese chat (reminder_id lleno = ya confirmado); borra lo caducado."""
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE expires_at <= ?", (now,))
        r = conn.execute(
            "SELECT payload, expires_at, reminder_id FROM pending_confirmations"
            " WHERE channel = ? AND chat_id = ? AND kind = 'reminder'",
            (channel, chat_id),
        ).fetchone()
    if not r:
        return None
    return {**json.loads(r["payload"]), "caduca": r["expires_at"], "reminder_id": r["reminder_id"]}


def clear_pending(channel: str, chat_id: str) -> bool:
    """Cancela lo que espera confirmación (lo ya confirmado no se toca)."""
    with connect() as conn:
        return conn.execute(
            "DELETE FROM pending_confirmations WHERE channel = ? AND chat_id = ?"
            " AND reminder_id IS NULL AND kind = 'reminder'",
            (channel, chat_id),
        ).rowcount > 0


def confirm(channel: str, chat_id: str) -> dict | None:
    """Convierte lo pendiente en recordatorio, una sola vez: leer, insertar y marcar van en la
    misma transacción, y un "sí" repetido devuelve el ya creado (ya_guardado=True)."""
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # dos "sí" a la vez: el segundo espera y ve reminder_id
        r = conn.execute(
            "SELECT payload, reminder_id FROM pending_confirmations"
            " WHERE channel = ? AND chat_id = ? AND expires_at > ? AND kind = 'reminder'",
            (channel, chat_id, now),
        ).fetchone()
        if not r:
            return None
        if r["reminder_id"]:
            again = r["reminder_id"]
        else:
            again = None
            p = json.loads(r["payload"])
            rid = _insert(conn, p["tarea"], p["due_at"], channel, chat_id, regla=p.get("regla"))
            conn.execute(
                "UPDATE pending_confirmations SET reminder_id = ? WHERE channel = ? AND chat_id = ?",
                (rid, channel, chat_id),
            )
    if again:
        saved = get(again)
        return {**saved, "ya_guardado": True} if saved else None
    return {**_saved(rid), "ya_guardado": False}


# ---------- pregunta de hora ambigua (pending_confirmations, kind 'hora') ----------


def preguntar_hora(channel: str, chat_id: str, datos: dict) -> None:
    """Guarda la pregunta «¿de la mañana o de la noche?» (reemplaza lo pendiente del chat)."""
    now = clock.now_utc()
    expires = now + timedelta(minutes=PREGUNTA_HORA_MIN)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_confirmations (channel, chat_id, kind, payload,"
            " created_at, expires_at, reminder_id) VALUES (?, ?, 'hora', ?, ?, ?, NULL)",
            (channel, chat_id, json.dumps(datos, ensure_ascii=False), now.isoformat(), expires.isoformat()),
        )
    log.info("Pregunta de hora pendiente en %s (caduca %s)", channel, expires.isoformat(timespec="seconds"))


def pregunta_hora(channel: str, chat_id: str) -> dict | None:
    """La pregunta de hora pendiente del chat (borra lo caducado)."""
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE expires_at <= ?", (now,))
        r = conn.execute(
            "SELECT payload FROM pending_confirmations WHERE channel = ? AND chat_id = ? AND kind = 'hora'",
            (channel, chat_id),
        ).fetchone()
    return json.loads(r["payload"]) if r else None


def olvidar_pregunta_hora(channel: str, chat_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE channel = ? AND chat_id = ? AND kind = 'hora'",
                     (channel, chat_id))


def resolver_hora(datos: dict, respuesta: str) -> dict | None:
    """Lo entendido (como interpret) con la hora que eligió, o None si la respuesta no dice am/pm
    ni la hora. Lanza NotUnderstood si esa hora ya pasó."""
    t = when.respuesta_ampm(respuesta, int(datos["h"]), int(datos["m"]))
    if t is None:
        return None
    now = clock.now_local()
    dt = datetime.combine(date.fromisoformat(datos["fecha"]), t)
    if t.hour == 0 and int(datos["h"]) == 12:  # medianoche: al terminar ese día
        dt += timedelta(days=1)
    regla = datos.get("regla")
    if regla:
        while dt <= now:
            dt = when.siguiente(regla, dt)
    elif dt <= now and not datos.get("con_dia"):
        dt += timedelta(days=1)
    return _result(dt, datos["tarea"], "reglas", now, regla=regla)


def list_pending() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pending_confirmations ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


# ---------- leer ----------


def _row(r) -> dict:
    d = dict(r)
    local = clock.utc_to_local(r["due_at"])
    d["due_local"] = local.strftime("%Y-%m-%d %H:%M")
    d["due_ampm"] = when.ampm(local)
    d["proximo_local"] = clock.utc_to_local(r["proximo_envio"]).strftime("%Y-%m-%d %H:%M") if r["proximo_envio"] else None
    d["regla"] = json.loads(r["regla"]) if r["regla"] else None
    d["estado_es"] = ESTADO_ES.get(r["estado"], r["estado"])
    d["abierto"] = r["estado"] in ABIERTOS
    return d


def get(rid: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    return _row(r) if r else None


def list_open(chat_id: str | None = None, limit: int = 50, sin_confirmar: bool = False) -> list[dict]:
    """Abiertos por fecha; con sin_confirmar=True también los que quedaron sin confirmar."""
    estados = ABIERTOS + (("sin_confirmar",) if sin_confirmar else ())
    marks = ",".join("?" * len(estados))
    sql = f"SELECT * FROM reminders WHERE estado IN ({marks})"
    args: list = list(estados)
    if chat_id:
        sql += " AND chat_id = ?"
        args.append(chat_id)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY due_at LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def ultimo_avisado(chat_id: str | None) -> dict | None:
    """El último recordatorio que ya sonó y espera respuesta (o quedó sin confirmar)."""
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM reminders WHERE estado IN ('avisado', 'esperando', 'sin_confirmar')"
            " AND (? IS NULL OR chat_id = ?) ORDER BY last_sent_at DESC LIMIT 1",
            (chat_id, chat_id),
        ).fetchone()
    return _row(r) if r else None


# ---------- cambiar de estado ----------


def _siguiente_de_serie(conn, r) -> int | None:
    """Crea la próxima ocurrencia de una serie (si no existe ya una abierta posterior)."""
    if not r["regla"]:
        return None
    ya = conn.execute(
        f"SELECT 1 FROM reminders WHERE serie_id = ? AND due_at > ? AND estado IN ({','.join('?' * len(ABIERTOS))})",
        (r["serie_id"], r["due_at"], *ABIERTOS),
    ).fetchone()
    if ya:
        return None
    regla = json.loads(r["regla"])
    local = when.siguiente(regla, clock.utc_to_local(r["due_at"]))
    ahora = clock.now_local()
    while local <= ahora:  # estuvo caído varios días: la próxima a futuro
        local = when.siguiente(regla, local)
    nid = _insert(conn, r["text"], clock.local_to_utc(local).isoformat(), r["channel"], r["chat_id"],
                  r["source_event_id"], regla, r["serie_id"])
    log.info("Recordatorio %s · serie %s: siguiente ocurrencia %s", nid, r["serie_id"], local.strftime("%Y-%m-%d %H:%M"))
    return nid


def cerrar(rid: int, estado: str, chat_id: str | None = None, respuesta: str | None = None) -> dict | None:
    """Pasa a hecho, cancelado o sin_confirmar (desde abierto; hecho también desde sin_confirmar).
    Con chat_id, solo si es de ese chat. Si es de una serie, crea la siguiente ocurrencia."""
    if estado not in CERRADOS:
        raise ValueError(f"estado de cierre inválido: {estado}")
    desde = ABIERTOS + (("sin_confirmar",) if estado in ("hecho", "cancelado") else ())  # sin confirmar: también se cancela
    now = clock.now_utc().isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute(
            f"SELECT * FROM reminders WHERE id = ? AND estado IN ({','.join('?' * len(desde))})"
            " AND (? IS NULL OR chat_id = ?)",
            (rid, *desde, chat_id, chat_id),
        ).fetchone()
        if not r:
            return None
        conn.execute(
            "UPDATE reminders SET estado = ?, proximo_envio = NULL, done_at = ?, ultima_respuesta = ? WHERE id = ?",
            (estado, now, respuesta or estado, rid),
        )
        if r["estado"] != "sin_confirmar":  # la siguiente ya se creó al quedar sin confirmar
            _siguiente_de_serie(conn, r)
    log.info("Recordatorio %s · %s (%s)", rid, estado, respuesta or "-")
    events.publish("reminder", {"id": rid})
    return get(rid)


def cancelar_serie(rid: int, chat_id: str | None = None) -> list[dict]:
    """Cancela todas las ocurrencias abiertas o sin confirmar de la serie de rid (sin crear la siguiente)."""
    r = get(rid)
    if not r or (chat_id and r["chat_id"] != chat_id):
        return []
    estados = ABIERTOS + ("sin_confirmar",)  # también lo que quedó sin confirmar: la serie entera se va
    marks = ",".join("?" * len(estados))
    with connect() as conn:
        ids = [x["id"] for x in conn.execute(
            f"SELECT id FROM reminders WHERE (serie_id = ? OR id = ?) AND estado IN ({marks})",
            (r["serie_id"] or rid, r["serie_id"] or rid, *estados)
        )]
        conn.execute(
            f"UPDATE reminders SET estado = 'cancelado', proximo_envio = NULL, done_at = ?,"
            f" ultima_respuesta = 'serie cancelada' WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})",
            (clock.now_utc().isoformat(), *ids),
        )
    log.info("Recordatorio %s · serie cancelada (%d ocurrencias)", rid, len(ids))
    return [get(i) for i in ids]


def posponer(rid: int, due_at: str, chat_id: str | None = None) -> dict | None:
    """Nueva hora para una ocurrencia abierta o sin confirmar: vuelve a empezar (pospuesto)."""
    due = datetime.fromisoformat(due_at)
    if due.tzinfo is None:
        raise ValueError("due_at debe incluir zona horaria (UTC)")
    due = due.astimezone(timezone.utc)
    now = clock.now_utc()
    estados = ABIERTOS + ("sin_confirmar",)
    with connect() as conn:
        n = conn.execute(
            "UPDATE reminders SET due_at = ?, estado = 'pospuesto', proximo_envio = ?, insistencias = 0,"
            " attempts = 0, last_sent_at = NULL, anticipacion_min = ?, ultima_respuesta = 'pospuesto'"
            f" WHERE id = ? AND estado IN ({','.join('?' * len(estados))}) AND (? IS NULL OR chat_id = ?)",
            (due.isoformat(), _primer_envio(now, due).isoformat(), int((due - now).total_seconds() // 60),
             rid, *estados, chat_id, chat_id),
        ).rowcount
    if n:
        log.info("Recordatorio %s · pospuesto", rid)
    return get(rid) if n else None


# ---------- programador (sin IA) ----------


def _aviso(r, clave: str, retrasado: bool) -> tuple[str, list | None]:
    """Texto (de profiles/isabelle.toml) y botones de cada aviso."""
    texto = persona.frase(f"recordatorios.{clave}", tarea=r["text"],
                          hora=when.ampm(clock.utc_to_local(r["due_at"])))
    if retrasado:
        texto = f"{persona.frase('recordatorios.retrasado', mayuscula=False)} {texto}"
    texto = persona.dosificar(texto)
    if clave == "previo":
        return texto, None
    return texto, [[{"texto": "Ya lo hice", "dato": f"rec:hecho:{r['id']}"},
                    {"texto": "Recuérdame", "dato": f"rec:posponer:{r['id']}"}]]


def _paso(conn, r, now: datetime) -> str | None:
    """Decide y escribe el siguiente paso de un recordatorio vencido. Devuelve la etapa o None."""
    due, programado = _utc(r["due_at"]), _utc(r["proximo_envio"])
    cada = timedelta(minutes=settings.REMINDER_RETRY_MIN)
    estado, n = r["estado"], r["insistencias"]
    if estado in ("creado", "pospuesto") and programado < due and now < due:
        clave, nuevo, proximo, n_nuevo = "previo", "avisado_previo", due, 0
    elif estado in ("creado", "pospuesto", "avisado_previo"):
        clave, nuevo, proximo, n_nuevo = "hora", "avisado", now + cada, 0
    elif estado in ("avisado", "esperando") and n < INSISTENCIAS:
        n_nuevo = n + 1
        clave = f"insistencia{n_nuevo}"
        nuevo, proximo = ("sin_confirmar", None) if n_nuevo >= INSISTENCIAS else ("esperando", now + cada)
    else:
        return None
    # Retraso respecto de la hora de ESTE aviso (si se saltó el previo, la referencia es la hora)
    referencia = due if clave == "hora" else programado
    retrasado = now - referencia > timedelta(minutes=RETRASO_MIN)
    texto, botones = _aviso(r, clave, retrasado)
    ok = conn.execute(
        "UPDATE reminders SET estado = ?, proximo_envio = ?, insistencias = ?, attempts = attempts + 1,"
        " last_sent_at = ? WHERE id = ? AND estado = ? AND proximo_envio = ?",
        (nuevo, proximo.isoformat() if proximo else None, n_nuevo, now.isoformat(), r["id"], estado, r["proximo_envio"]),
    ).rowcount
    if not ok:  # otro proceso ya lo envió: nada que hacer
        return None
    outbox.put(conn, r["channel"], r["chat_id"], "reminder", str(r["id"]), texto, botones)
    if nuevo == "sin_confirmar":
        _siguiente_de_serie(conn, r)
    return clave + (" (retrasado)" if retrasado else "")


def proximo() -> datetime | None:
    """Cuándo toca el próximo aviso (para que el programador despierte justo a esa hora)."""
    marks = ",".join("?" * len(ABIERTOS))
    with connect() as conn:
        r = conn.execute(f"SELECT MIN(proximo_envio) FROM reminders WHERE estado IN ({marks}) AND proximo_envio IS NOT NULL",
                         ABIERTOS).fetchone()[0]
    return _utc(r) if r else None


def atrasados(segundos: int = ATRASO_S) -> list[int]:
    """Recordatorios abiertos cuyo aviso venció hace más de `segundos` y no salió al buzón."""
    marks = ",".join("?" * len(ABIERTOS))
    limite = (clock.now_utc() - timedelta(seconds=segundos)).isoformat()
    with connect() as conn:
        return [r["id"] for r in conn.execute(
            f"SELECT id FROM reminders WHERE estado IN ({marks}) AND proximo_envio IS NOT NULL AND proximo_envio < ?",
            (*ABIERTOS, limite))]


def due_step() -> dict:
    """Programador: pasa al buzón los avisos que tocan (previo, a la hora, insistencias)."""
    now = clock.now_utc()
    marks = ",".join("?" * len(ABIERTOS))
    with connect() as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM reminders WHERE estado IN ({marks}) AND proximo_envio <= ? ORDER BY proximo_envio",
            (*ABIERTOS, now.isoformat()),
        )]
    sent = 0
    for rid in ids:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            r = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
            if not r or r["estado"] not in ABIERTOS or not r["proximo_envio"] or _utc(r["proximo_envio"]) > now:
                continue
            etapa = _paso(conn, r, now)
        if etapa:
            sent += 1
            log.info("Recordatorio %s · aviso «%s» al buzón de %s", rid, etapa, r["channel"])
    return {"avisos": sent}
