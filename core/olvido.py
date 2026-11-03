"""«Olvida eso» (Fase 2E): borrar de verdad lo que el jefe pide, siempre con confirmación por texto.

Pedido en lenguaje natural (reglas, sin IA): «olvida eso», «borra lo de Rosa», «elimina la nota de
Tralvex», «olvida todo lo de X». Si hay varias notas y no dijo «todo», lista candidatos numerados
y espera «la 2» / «todas». Máximo [olvido].max_items por orden; si hay más, pide acotar.

Confirmación SIEMPRE por texto («sí» y nada más ejecuta), con lo que se va a borrar a la vista:
la nota o el archivo, lo que se anotó en su ficha sobre eso (no se rastrea por nota: se lista y
se confirma junto), los recordatorios ligados que se cancelan. Cualquier otra respuesta descarta
la pregunta y se atiende normal.

Borrado en cascada: original en data/raw y su copia en el espejo de respaldos (las copias de la
base rotan: «puede quedar en los respaldos de los últimos 7 días»), trozos, índice FTS, vectores,
resúmenes, el evento, los ítems de la ficha, las menciones en el historial de la charla y las
frases del resumen corriente que lo nombran. Cada borrado queda en la tabla olvidos con tipo y
fecha, sin contenido.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

from config import settings
from core import charla, citations, clock, events, memory, pendientes, persona, reminders, verify
from core.db import connect

log = logging.getLogger("nexus.olvido")

PREGUNTA_MIN = 15
RE_PEDIDO = re.compile(
    r"^(?:isabelle\s+)?(?:por favor\s+)?(?:olvida(?:te)?(?:\s+de)?|olvidalo|olvidala|borra(?:lo|la)?|elimina(?:lo|la)?"
    r"|quita(?:lo|la)?|saca(?:lo|la)? de tu memoria)\b\s*(?P<resto>.*)$")
RE_ESO = re.compile(r"^(?:eso|esto|lo ultimo|la ultima nota|lo que te (?:dije|acabo de decir|mande)|lo anterior)?(?:\s+por favor)?$")
RE_TODO = re.compile(r"^(?:todo|todas las notas|todos los archivos|todas)\s+(?:lo\s+)?(?:de|sobre|acerca de)\s+")
RE_RELLENO = re.compile(r"^(?:lo|la|las|los|el|una?)?\s*(?:nota|notas|archivo|archivos|dato|datos|info|informacion|que te dije)?\s*"
                        r"(?:de|del|sobre|acerca de|con)?\s+")
RE_SI = re.compile(r"^si\b")
RE_NO = re.compile(r"^(no|nop|nada|cancela|cancelalo|mejor no|dejalo|olvidalo no|ninguna|ninguno)\b")
RE_TODAS = re.compile(r"\b(todas|todos|ambas|ambos|las dos|los dos|las tres)\b")


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    sin = "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9ñ]+", sin))


def _original(texto: str, termino: str) -> str:
    """El término como lo escribió él («Rosa», no «rosa»): las palabras de plano() van en el mismo orden."""
    originales = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", texto))
    n = len(termino.split())
    if n and len(originales) >= n and plano(" ".join(originales[-n:])) == termino:
        return " ".join(originales[-n:])
    return termino


def _cfg() -> dict:
    return persona.get().get("olvido") or {"max_items": 10, "respaldos": "Puede quedar en los respaldos de los últimos 7 días."}


# ---------- qué se borra ----------


def _evento(conn, eid: str) -> dict | None:
    r = conn.execute(
        "SELECT e.id, e.created_at, e.filename, e.kind, e.source, e.raw_path,"
        " (SELECT content FROM chunks c WHERE c.event_id = e.id AND c.position >= 0 ORDER BY c.position LIMIT 1) AS primero"
        " FROM events e WHERE e.id = ?", (eid,)).fetchone()
    return dict(r) if r else None


def _breve(ev: dict) -> str:
    return citations.legible(ev["filename"] or "", ev["created_at"], ev.get("primero") or "")


def _ultimo() -> list[str]:
    with connect() as conn:
        r = conn.execute("SELECT id FROM events ORDER BY created_at DESC LIMIT 1").fetchone()
    return [r["id"]] if r else []


def _buscar(termino: str, limite: int) -> list[str]:
    """Eventos que nombran el término (en sus trozos o en el nombre del archivo), recientes primero."""
    palabras = [w for w in re.findall(r"\w+", termino) if w]
    if not palabras:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.event_id AS id, e.created_at FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
            " JOIN events e ON e.id = c.event_id WHERE chunks_fts MATCH ?"
            " UNION SELECT id, created_at FROM events WHERE lower(filename) LIKE ?"
            " ORDER BY created_at DESC LIMIT ?",
            (" ".join(f'"{w}"' for w in palabras), f"%{termino.lower()}%", limite),
        ).fetchall()
    return [r["id"] for r in rows]


def _ficha_de(claves: list[str]) -> list[tuple[str, str]]:
    """(campo, ítem) de la ficha que nombran alguna de las claves."""
    claves = [plano(c) for c in claves if plano(c)]
    out = []
    for campo, items in charla.ficha().items():
        for it in items:
            if any(re.search(rf"\b{re.escape(c)}\b", plano(str(it))) for c in claves):
                out.append((campo, str(it)))
    return out


def _recordatorios_de(ids: list[str], claves: list[str]) -> list[dict]:
    """Recordatorios abiertos ligados a esos eventos o que nombran las claves."""
    out = []
    for r in reminders.list_open(limit=200):
        texto_ = plano(r.get("text") or "")
        if r.get("source_event_id") in ids or any(re.search(rf"\b{re.escape(plano(c))}\b", texto_) for c in claves if plano(c)):
            out.append({"id": r["id"], "texto": r["text"]})
    return out


def _claves(termino: str | None, evs: list[dict]) -> list[str]:
    """Con qué palabras se buscan menciones: el término, o los nombres propios de la nota («eso»)."""
    if termino:
        return [termino]
    nombres: list[str] = []
    for ev in evs:
        nombres += [n for n in verify.names(ev.get("primero") or "") if len(n) >= 3]
    return list(dict.fromkeys(nombres))[:5]


def _plan(ids: list[str], termino: str | None) -> dict:
    with connect() as conn:
        evs = [e for e in (_evento(conn, i) for i in ids) if e]
    claves = _claves(termino, evs)
    return {
        "ids": [e["id"] for e in evs], "termino": termino, "claves": claves,
        "breves": [_breve(e) for e in evs],
        "ficha": _ficha_de(claves),
        "recordatorios": _recordatorios_de([e["id"] for e in evs], claves),
    }


def _confirmacion(plan: dict) -> str:
    t = persona.tratamiento()
    partes = []
    if plan["breves"]:
        partes.append(" y ".join(f"la {b}" for b in plan["breves"]) if len(plan["breves"]) <= 2
                      else f"{len(plan['breves'])} notas sobre «{plan['termino']}»")
    if plan["ficha"]:
        partes.append("de lo que sé de usted: " + "; ".join(f"«{i}»" for _, i in plan["ficha"]))
    if plan["recordatorios"]:
        partes.append("y cancelo " + ("el recordatorio " if len(plan["recordatorios"]) == 1 else "los recordatorios ")
                      + ", ".join(f"«{r['texto']}»" for r in plan["recordatorios"]))
    que = ", ".join(partes)
    if plan["termino"]:
        que += f" (y lo que hablamos de «{plan['termino']}»)"
    return (f"¿Confirma que olvide {que}? Esto lo borra del todo, {t}. {_cfg()['respaldos']} "
            "Escríbame «sí» para borrarlo.")


# ---------- conversación ----------


def pedir(texto: str, channel: str, chat_id: str) -> str | None:
    """Si es un pedido de olvidar, la respuesta (lista o confirmación); si no, None."""
    p = plano(texto)
    m = RE_PEDIDO.match(p)
    if not m or re.search(r"\brecordatorio", p):
        return None  # «borra el recordatorio 3» lo atiende la conversación de recordatorios
    resto = m.group("resto").strip()
    maximo = int(_cfg().get("max_items", 10))
    t = persona.tratamiento()
    if RE_ESO.match(resto):
        ids = _ultimo()
        if not ids:
            return f"No tengo nada guardado que olvidar, {t}."
        plan = _plan(ids, None)
    else:
        todo = bool(RE_TODO.match(resto))
        termino = _original(texto, RE_RELLENO.sub("", RE_TODO.sub("", resto), count=1).strip() or resto)
        ids = _buscar(termino, maximo + 1)
        if len(ids) > maximo:
            return (f"Sobre «{termino}» tengo más de {maximo} cosas, {t}; no borro tanto de una vez. "
                    "Dígame algo más específico (por ejemplo, de qué fecha o de qué tema).")
        plan = _plan(ids, termino)
        if not plan["ids"] and not plan["ficha"] and not plan["recordatorios"] and not _menciones(plan["claves"], sin_ultimo=True):
            return f"No encuentro nada sobre «{termino}» en mi memoria, {t}."
        if len(plan["ids"]) > 1 and not todo:
            pendientes.guardar(channel, chat_id, "olvido", {"etapa": "elegir", "ids": plan["ids"], "termino": termino},
                               PREGUNTA_MIN)
            lista = "\n".join(f"{i}. {b[:1].upper()}{b[1:]}" for i, b in enumerate(plan["breves"], 1))
            return (f"Sobre «{termino}» tengo {len(plan['ids'])} cosas, {t}:\n{lista}\n"
                    "¿Cuál olvido? Dígame el número (por ejemplo «la 2») o «todas».")
    pendientes.guardar(channel, chat_id, "olvido", {"etapa": "confirmar", **plan}, PREGUNTA_MIN)
    return _confirmacion(plan)


def resolver(texto: str, channel: str, chat_id: str) -> str | None:
    """Su respuesta a la lista o a la confirmación. None si no responde eso: la pregunta se
    descarta y el mensaje se atiende normal (nunca se borra sin «sí»)."""
    datos = pendientes.tomar(channel, chat_id, "olvido")
    if not datos:
        return None
    p = plano(texto)
    t = persona.tratamiento()
    if RE_NO.match(p):
        return f"De acuerdo, {t}: no borro nada."
    if datos["etapa"] == "elegir":
        if RE_TODAS.search(p):
            elegidos = datos["ids"]
        else:
            nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", p)]
            if not nums or len(p.split()) > 8:
                return None
            if any(not 1 <= n <= len(datos["ids"]) for n in nums):
                pendientes.guardar(channel, chat_id, "olvido", datos, PREGUNTA_MIN)
                return f"Esa opción no está en la lista, {t}. Dígame un número del 1 al {len(datos['ids'])}."
            elegidos = [datos["ids"][n - 1] for n in dict.fromkeys(nums)]
        plan = _plan(elegidos, datos["termino"])
        pendientes.guardar(channel, chat_id, "olvido", {"etapa": "confirmar", **plan}, PREGUNTA_MIN)
        return _confirmacion(plan)
    if not RE_SI.match(p):
        return None
    hecho = ejecutar(datos)
    return _cierre(hecho)


def _cierre(h: dict) -> str:
    t = persona.tratamiento()
    partes = []
    if h["nota"] or h["archivo"]:
        n = h["nota"] + h["archivo"]
        partes.append(f"{n} nota{'s' if n > 1 else ''}" if not h["archivo"] else f"{n} elemento{'s' if n > 1 else ''} (con su original)")
    if h["ficha"]:
        partes.append(f"{h['ficha']} dato{'s' if h['ficha'] > 1 else ''} de su ficha")
    if h["charla"]:
        partes.append(f"{h['charla']} mensaje{'s' if h['charla'] > 1 else ''} de nuestra charla")
    texto_ = f"Listo, {t}: olvidé " + (", ".join(partes) if partes else "lo que me pidió")
    if h["recordatorios"]:  # sin repetir lo olvidado: el cierre queda en la charla
        n = len(h["recordatorios"])
        texto_ += f" y cancelé {n} recordatorio{'s' if n > 1 else ''} ligado{'s' if n > 1 else ''}"
    return f"{texto_}. {_cfg()['respaldos']}"


# ---------- borrar ----------


def _menciones(claves: list[str], extra: list[str] | None = None, sin_ultimo: bool = False) -> list[int]:
    """Mensajes del historial de la charla que nombran las claves o contienen el texto de la nota.
    sin_ultimo: sin el mensaje que lo está pidiendo (para saber si hay algo más que borrar)."""
    ids: set[int] = set()
    with connect() as conn:
        for c in claves:
            palabras = re.findall(r"\w+", c)
            if palabras:
                ids |= {r["id"] for r in conn.execute("SELECT rowid AS id FROM charla_fts WHERE charla_fts MATCH ?",
                                                      (" ".join(f'"{w}"' for w in palabras),))}
        for frag in extra or []:
            frag = " ".join(frag.split())[:60]
            if len(frag) >= 12:
                ids |= {r["id"] for r in conn.execute("SELECT id FROM charla WHERE texto LIKE ?", (f"%{frag}%",))}
    if sin_ultimo and (u := _ultimo_jefe()):
        ids.discard(u)
    return sorted(ids)


def _ultimo_jefe() -> int | None:
    with connect() as conn:
        return conn.execute("SELECT MAX(id) FROM charla WHERE rol = 'jefe'").fetchone()[0]


def _espejo(raw_path: str) -> Path | None:
    """La copia del original en el espejo de respaldos (backups/raw), si existe."""
    try:
        rel = Path(raw_path).relative_to(Path(settings.DATA_DIR) / "raw")
    except ValueError:
        return None
    return Path(settings.BACKUP_DIR) / "raw" / rel


def _registrar(conn, tipo: str, n: int = 1) -> None:
    now = clock.now_utc().isoformat()
    for _ in range(n):
        conn.execute("INSERT INTO olvidos (created_at, tipo) VALUES (?, ?)", (now, tipo))


def ejecutar(plan: dict) -> dict:
    """Borra todo lo del plan (ya confirmado). Devuelve cuántas cosas de cada tipo."""
    hecho = {"nota": 0, "archivo": 0, "ficha": 0, "charla": 0, "recordatorios": []}
    archivos: list[Path] = []
    fragmentos: list[str] = []
    with connect() as conn:
        for eid in plan["ids"]:
            ev = _evento(conn, eid)
            if not ev:
                continue
            fragmentos.append(ev.get("primero") or "")
            memory.delete_chunks(conn, eid)
            conn.execute("DELETE FROM digests WHERE event_id = ?", (eid,))
            conn.execute("UPDATE reminders SET source_event_id = NULL WHERE source_event_id = ?", (eid,))
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))
            archivos += [Path(ev["raw_path"])] + ([e] if (e := _espejo(ev["raw_path"])) else [])
            tipo = "nota" if ev["kind"] == "text" else "archivo"
            hecho[tipo] += 1
            _registrar(conn, tipo)
    for f in archivos:
        try:
            f.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("No pude borrar un original (%s)", type(exc).__name__)
    # Ficha: se quitan los ítems listados en la confirmación
    if plan["ficha"]:
        quitar = {(c, i) for c, i in plan["ficha"]}
        f = charla.ficha()
        nueva = {c: [i for i in items if (c, str(i)) not in quitar] for c, items in f.items()}
        hecho["ficha"] = sum(len(v) for v in f.values()) - sum(len(v) for v in nueva.values())
        with connect() as conn:
            charla._set(conn, charla.K_FICHA, json.dumps(nueva, ensure_ascii=False))
            _registrar(conn, "ficha", hecho["ficha"])
    # Charla: menciones y frases del resumen corriente que lo nombran
    menciones = _menciones(plan["claves"], fragmentos)
    with connect() as conn:
        if menciones:
            conn.execute(f"DELETE FROM charla WHERE id IN ({','.join('?' * len(menciones))})", menciones)
            _registrar(conn, "charla", len(menciones))
        hecho["charla"] = len(menciones)
        resumen = charla._kv(conn, charla.K_RESUMEN) or ""
        nuevo = _sin_menciones(resumen, plan["claves"])
        if nuevo != resumen:
            charla._set(conn, charla.K_RESUMEN, nuevo)
            _registrar(conn, "resumen")
    for r in plan["recordatorios"]:
        actual = reminders.get(r["id"])
        if not actual or actual["estado"] not in reminders.ABIERTOS:
            continue
        hechos = reminders.cancelar_serie(r["id"]) if actual.get("regla") else [reminders.cerrar(r["id"], "cancelado", respuesta="olvido")]
        if any(hechos):
            hecho["recordatorios"].append(r["texto"])
            with connect() as conn:
                _registrar(conn, "recordatorio")
    events.publish("olvido", {k: (len(v) if isinstance(v, list) else v) for k, v in hecho.items()})
    log.info("Olvido: %d nota(s), %d archivo(s), %d de la ficha, %d de la charla, %d recordatorio(s)",
             hecho["nota"], hecho["archivo"], hecho["ficha"], hecho["charla"], len(hecho["recordatorios"]))
    return hecho


def _sin_menciones(resumen: str, claves: list[str]) -> str:
    """El resumen sin las frases que nombran lo olvidado."""
    if not resumen or not claves:
        return resumen
    frases = re.split(r"(?<=[.!?])\s+", resumen)
    quedan = [f for f in frases if not any(re.search(rf"\b{re.escape(plano(c))}\b", plano(f)) for c in claves if plano(c))]
    return " ".join(quedan).strip()
