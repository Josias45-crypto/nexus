"""Memoria de la conversación con el jefe, en tres capas:

(a) Historial completo (tabla charla, con búsqueda por texto en charla_fts y por fecha).
(b) Ficha del jefe: lo importante que él contó, ordenado por campos. Sale SOLO de lo que dice él
    (lo que dice Isabelle nunca es un dato sobre él; solo sirve para entender a qué responde).
    Se valida campo por campo; si algo falla se conserva la versión anterior.
(c) Resumen corriente de la charla (máx. RESUMEN_MAX palabras).

La ficha y el resumen viven en kv y van SEPARADOS del conocimiento con fuentes (chunks): el
modelo los recibe en su propio bloque y nunca como una fuente citable.

La actualización corre en segundo plano solo cuando nadie espera respuesta: si llega un mensaje,
se cancela (pausar) y se reintenta en la siguiente revisión del worker.
"""

import asyncio
import json
import logging
from datetime import date, timedelta

from core import clock
from core.db import connect

log = logging.getLogger("nexus.charla")

ROLES = ("jefe", "isabelle")
RECIENTES = 12  # mensajes de la charla que entran en cada turno
TEXTO_TURNO_MAX = 600  # caracteres por mensaje en el contexto
POR_LOTE = 40  # mensajes nuevos que se leen por actualización
ESPERA_S = 45  # segundos sin mensajes del jefe antes de actualizar (puede seguir escribiendo)
LISTA_MAX = 10
TEXTO_MAX = 200
RESUMEN_MAX = 80
FALLOS_MAX = 3  # tras N fallos seguidos con el mismo lote, se salta (no gasta para siempre)

# campo -> descripción para el modelo; todos son listas de frases cortas
CAMPOS = {
    "personas": "personas que nombra y quiénes son para él (p. ej. «Piter: proveedor de laptops»)",
    "negocio": "datos estables de su negocio (rubro, local, productos, cómo vende)",
    "preferencias": "gustos y formas de trabajar que dijo (horarios, cómo quiere que le hablen...)",
    "habitos": "hábitos y rutinas que contó o quiere formar",
    "metas": "metas y proyectos que mencionó",
    "pendientes": "cosas que él dijo que haría (no los recordatorios: esos los lleva el sistema)",
    "datos": "otros datos personales que él contó (cumpleaños, ciudad, estudios...)",
}
K_FICHA, K_ANTERIOR, K_RESUMEN, K_HASTA, K_FALLOS = (
    "jefe:ficha", "jefe:ficha_anterior", "jefe:resumen", "jefe:memoria_hasta", "jefe:memoria_fallos",
)

INSTRUCCIONES = f"""Mantienes la memoria de Isabelle sobre su jefe. Actualiza la FICHA del jefe y el
RESUMEN de la conversación y devuelve SOLO un JSON.

Ficha:
- Los datos nuevos salen SOLO de «MENSAJES DEL JEFE». Lo que dijo Isabelle nunca es un dato sobre
  él; úsalo solo para entender a qué responde (si ella preguntó «¿a qué hora abre?» y él dijo «a
  las 9», su local abre a las 9).
- Conserva lo que sigue valiendo de la FICHA ACTUAL. Si algo nuevo la contradice, vale lo nuevo.
- No inventes ni supongas. Solo lo importante y estable: no anotes saludos, charla pasajera,
  preguntas que él hizo ni recordatorios (esos los lleva el sistema).
- Cada elemento, una frase corta. Campos: {", ".join(f"{k} ({v})" for k, v in CAMPOS.items())}.

Resumen: un párrafo de máximo {RESUMEN_MAX} palabras con de qué vienen hablando y qué quedó
pendiente, sin inventar.

Formato exacto, con TODOS los campos:
{{"ficha": {json.dumps({k: [] for k in CAMPOS})}, "resumen": ""}}"""

_tarea: asyncio.Task | None = None


# ---------- (a) historial ----------


def agregar(rol: str, texto: str, chat_id: str | None = None, accion: str | None = None) -> int:
    if rol not in ROLES:
        raise ValueError(f"rol inválido: {rol}")
    with connect() as conn:
        return conn.execute(
            "INSERT INTO charla (created_at, chat_id, rol, texto, accion) VALUES (?, ?, ?, ?, ?)",
            (clock.now_utc().isoformat(), chat_id, rol, texto, accion),
        ).lastrowid


def recientes(n: int = RECIENTES, chat_id: str | None = None) -> list[dict]:
    """Últimos n mensajes en orden cronológico (de ese chat si se indica)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, rol, texto FROM charla WHERE (? IS NULL OR chat_id = ?)"
            " ORDER BY id DESC LIMIT ?",
            (chat_id, chat_id, n),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _dia_utc(d: date, fin: bool = False) -> str:
    from datetime import datetime

    local = datetime.combine(d + timedelta(days=1) if fin else d, datetime.min.time())
    return clock.local_to_utc(local).isoformat()


def buscar(q: str | None = None, desde: date | None = None, hasta: date | None = None,
           limite: int = 50) -> list[dict]:
    """Busca en el historial por palabras (FTS5) y/o por días locales [desde, hasta]."""
    sql = "SELECT c.id, c.created_at, c.rol, c.texto FROM charla c"
    where, args = [], []
    if q and q.strip():
        palabras = [w for w in q.replace('"', " ").split() if w]
        sql += " JOIN charla_fts f ON f.rowid = c.id"
        where.append("charla_fts MATCH ?")
        args.append(" ".join(f'"{w}"' for w in palabras))
    if desde:
        where.append("c.created_at >= ?")
        args.append(_dia_utc(desde))
    if hasta:
        where.append("c.created_at < ?")
        args.append(_dia_utc(hasta, fin=True))
    if where:
        sql += " WHERE " + " AND ".join(where)
    with connect() as conn:
        rows = conn.execute(sql + " ORDER BY c.id DESC LIMIT ?", (*args, limite)).fetchall()
    return [{**dict(r), "hora_local": clock.utc_to_local(r["created_at"]).strftime("%Y-%m-%d %H:%M")} for r in rows]


# ---------- (b) ficha y (c) resumen ----------


def _kv(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def _set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def vacia() -> dict:
    return {k: [] for k in CAMPOS}


def ficha() -> dict:
    with connect() as conn:
        raw = _kv(conn, K_FICHA)
    f = vacia()
    try:
        guardada = json.loads(raw) if raw else {}
        if isinstance(guardada, dict):
            f.update({k: v for k, v in guardada.items() if k in CAMPOS and isinstance(v, list)})
    except ValueError:
        pass
    return f


def resumen() -> str:
    with connect() as conn:
        return _kv(conn, K_RESUMEN) or ""


def ficha_texto(f: dict | None = None) -> str:
    f = f or ficha()
    return "\n".join(f"{k.capitalize()}: {'; '.join(v)}" for k, v in f.items() if v)


def _recortar_resumen(texto: str) -> str:
    palabras = texto.split()
    if len(palabras) <= RESUMEN_MAX:
        return " ".join(palabras)
    corto = " ".join(palabras[:RESUMEN_MAX])
    fin = max(corto.rfind(c) for c in ".!?")
    return corto[: fin + 1] if fin > 0 else corto


def validar(datos) -> tuple[dict, str]:
    """{"ficha": {todos los campos: listas de textos}, "resumen": "..."} -> (ficha, resumen).
    Lanza ValueError si algo no cuadra (entonces se conserva la versión anterior)."""
    if not isinstance(datos, dict) or not isinstance(datos.get("ficha"), dict):
        raise ValueError("falta 'ficha'")
    if not isinstance(datos.get("resumen"), str):
        raise ValueError("falta 'resumen'")
    entrada = datos["ficha"]
    faltan = [c for c in CAMPOS if c not in entrada]
    if faltan:
        raise ValueError(f"faltan campos: {', '.join(faltan)}")
    f = {}
    for campo in CAMPOS:
        valor = entrada[campo]
        if not isinstance(valor, list):
            raise ValueError(f"'{campo}' debe ser una lista")
        items = []
        for v in valor:
            if isinstance(v, bool) or not isinstance(v, (str, int, float)):
                raise ValueError(f"'{campo}' debe tener solo textos")
            t = " ".join(str(v).split())[:TEXTO_MAX]
            if t and t.lower() not in {i.lower() for i in items}:
                items.append(t)
        f[campo] = items[-LISTA_MAX:]
    return f, _recortar_resumen(datos["resumen"])


def _cargar_json(texto: str):
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ValueError("no hay JSON")
    return json.loads(texto[inicio : fin + 1])


def _contenido(f: dict, res: str, mensajes: list[dict]) -> str:
    del_jefe = "\n".join(f"- {m['texto']}" for m in mensajes if m["rol"] == "jefe") or "(ninguno)"
    charla_txt = "\n".join(f"{'Jefe' if m['rol'] == 'jefe' else 'Isabelle'}: {m['texto']}" for m in mensajes)
    return (
        f"FICHA ACTUAL:\n{json.dumps(f, ensure_ascii=False)}\n\nRESUMEN ACTUAL:\n{res or '(vacío)'}\n\n"
        f"MENSAJES DEL JEFE (única fuente nueva de datos para la ficha):\n{del_jefe}\n\n"
        f"CONVERSACIÓN (para el resumen y para entender a qué responde él):\n{charla_txt}"
    )


def pendientes() -> list[dict]:
    """Mensajes nuevos desde la última actualización (hasta POR_LOTE)."""
    with connect() as conn:
        hasta = int(_kv(conn, K_HASTA) or 0)
        rows = conn.execute(
            "SELECT id, created_at, rol, texto FROM charla WHERE id > ? ORDER BY id LIMIT ?",
            (hasta, POR_LOTE),
        ).fetchall()
    return [dict(r) for r in rows]


async def actualizar() -> bool:
    """Una actualización de ficha y resumen con los mensajes nuevos. True si guardó algo."""
    from providers.openrouter import get_client

    mensajes = pendientes()
    if not any(m["rol"] == "jefe" for m in mensajes):
        if mensajes:
            with connect() as conn:
                _set(conn, K_HASTA, str(mensajes[-1]["id"]))
        return False
    actual, res = ficha(), resumen()
    texto = ""
    try:
        texto = await get_client().chat(
            [{"role": "system", "content": INSTRUCCIONES},
             {"role": "user", "content": _contenido(actual, res, mensajes)}],
            temperature=0, max_tokens=1500,
        )
        nueva, nuevo_resumen = validar(_cargar_json(texto))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        with connect() as conn:
            fallos = int(_kv(conn, K_FALLOS) or 0) + 1
            _set(conn, K_FALLOS, str(fallos))
            if fallos >= FALLOS_MAX:  # el lote no se deja procesar: se salta para no gastar en bucle
                _set(conn, K_HASTA, str(mensajes[-1]["id"]))
                _set(conn, K_FALLOS, "0")
        motivo = str(exc)[:80] if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        log.warning("Memoria del jefe sin cambios (%s; fallo %d de %d)", motivo, fallos, FALLOS_MAX)
        return False
    with connect() as conn:
        _set(conn, K_ANTERIOR, json.dumps(actual, ensure_ascii=False))
        _set(conn, K_FICHA, json.dumps(nueva, ensure_ascii=False))
        _set(conn, K_RESUMEN, nuevo_resumen)
        _set(conn, K_HASTA, str(mensajes[-1]["id"]))
        _set(conn, K_FALLOS, "0")
    log.info("Memoria del jefe actualizada (%d mensajes nuevos)", len(mensajes))
    return True


def pausar() -> None:
    """Llegó un mensaje: si la memoria se está actualizando, se cancela (la respuesta va primero)."""
    if _tarea and not _tarea.done():
        _tarea.cancel()
        log.info("Memoria del jefe pospuesta: llegó un mensaje")


def _jefe_escribio_hace_poco() -> bool:
    with connect() as conn:
        r = conn.execute("SELECT created_at FROM charla WHERE rol = 'jefe' ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return False
    from datetime import datetime

    return (clock.now_utc() - datetime.fromisoformat(r["created_at"])).total_seconds() < ESPERA_S


async def revisar(ocupado) -> dict:
    """Paso del worker: actualiza si hay mensajes nuevos, nadie espera respuesta (`ocupado()`),
    el jefe no está escribiendo justo ahora y OpenRouter no está en pausa tras un fallo."""
    global _tarea
    from providers.openrouter import get_client

    cliente = get_client()
    if cliente.paused_for() or not cliente.sano():  # tras un fallo, primero la digestión o el jefe
        return {}
    if ocupado() or _jefe_escribio_hace_poco() or not pendientes():
        return {}
    _tarea = asyncio.create_task(actualizar())
    try:
        return {"memoria_actualizada": await _tarea}
    except asyncio.CancelledError:
        if asyncio.current_task() and asyncio.current_task().cancelling():
            raise  # se apaga el worker
        return {"memoria_pospuesta": True}
    finally:
        _tarea = None
