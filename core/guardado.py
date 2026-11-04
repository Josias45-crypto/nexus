"""«Lo guardado» (Fase 5): lista, detalle y descarga de los originales y su análisis.

Los originales se sirven siempre como descarga (Content-Disposition: attachment y
application/octet-stream con nosniff y CSP sandbox): nunca se ejecuta un HTML ni un SVG subido.
Las rutas salen de la base, no del pedido, y se comprueba que el archivo esté dentro de data/raw.
De las fotos hay una vista previa re-codificada como JPEG (no el archivo subido).
"""

import io
import json
import re
from pathlib import Path

from config import settings
from core import medios
from core.db import connect

RAW = (Path(settings.DATA_DIR) / "raw").resolve()
RE_ID = re.compile(r"^[0-9a-f]{32}$")
TIPOS = {"notas": ("text",), "documentos": ("document",), "fotos": ("image",), "audios": ("audio",), "videos": ("video",)}
TEXTO_MAX = 4000
VISTA_LADO = 900


class NoEncontrado(LookupError):
    pass


def _fila(conn, eid: str):
    if not RE_ID.match(eid or ""):
        raise NoEncontrado("No existe.")
    r = conn.execute("SELECT id, kind, filename, mime, size, created_at, status, error, raw_path FROM events WHERE id = ?",
                     (eid,)).fetchone()
    if not r:
        raise NoEncontrado("No existe.")
    return r


def lista(tipo: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    where, args = [], []
    if tipo:
        kinds = TIPOS.get(tipo)
        if not kinds:
            raise ValueError(f"Tipo desconocido: {tipo}")
        where.append(f"e.kind IN ({','.join('?' * len(kinds))})")
        args += kinds
    if q and q.strip():
        palabras = [w for w in re.findall(r"\w+", q) if len(w) >= 2][:6]
        if palabras:
            fts = " ".join(f'"{w}"' for w in palabras)
            where.append("(e.filename LIKE ? OR e.id IN (SELECT c.event_id FROM chunks c JOIN chunks_fts f ON f.rowid = c.id"
                         " WHERE chunks_fts MATCH ?))")
            args += [f"%{q.strip()}%", fts]
    sql = " WHERE " + " AND ".join(where) if where else ""
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM events e{sql}", args).fetchone()[0]
        rows = conn.execute(
            "SELECT e.id, e.kind, e.filename, e.size, e.created_at, e.status, m.descripcion, m.tipo AS medio,"
            " (SELECT substr(c.content, 1, 160) FROM chunks c WHERE c.event_id = e.id AND c.position >= 0 ORDER BY c.position LIMIT 1) AS inicio"
            f" FROM events e LEFT JOIN medios m ON m.event_id = e.id{sql}"
            " ORDER BY e.created_at DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def detalle(eid: str) -> dict:
    with connect() as conn:
        e = dict(_fila(conn, eid))
        trozos = conn.execute("SELECT content FROM chunks WHERE event_id = ? AND position >= 0 ORDER BY position",
                              (eid,)).fetchall()
        d = conn.execute("SELECT summary, concepts FROM digests WHERE event_id = ? AND status = 'done'", (eid,)).fetchone()
    e.pop("raw_path")
    m = medios.get(eid)
    texto = "\n\n".join(t["content"] for t in trozos)
    return {
        **e, "trozos": len(trozos),
        "texto": texto[:TEXTO_MAX], "texto_recortado": len(texto) > TEXTO_MAX,
        "resumen": d["summary"] if d else None, "conceptos": json.loads(d["concepts"] or "[]") if d else [],
        "analisis": {k: m[k] for k in ("tipo", "estado", "clase", "descripcion", "texto", "datos", "ilegible", "notas",
                                       "duracion_s", "transcrito_s", "confirmado")} if m else None,
        "vista": e["kind"] == "image",
    }


def original(eid: str) -> tuple[Path, str]:
    """(ruta, nombre) del original, comprobando que esté dentro de data/raw."""
    with connect() as conn:
        r = _fila(conn, eid)
    path = Path(r["raw_path"]).resolve()
    if not path.is_relative_to(RAW) or not path.is_file():
        raise NoEncontrado("El original no está disponible.")
    return path, r["filename"] or "original"


def vista(eid: str) -> bytes:
    """Vista previa de una foto: re-codificada como JPEG (nunca el archivo tal cual)."""
    with connect() as conn:
        r = _fila(conn, eid)
    if r["kind"] != "image":
        raise NoEncontrado("Solo las fotos tienen vista previa.")
    from PIL import Image, ImageOps

    path, _ = original(eid)
    try:
        with Image.open(path) as img:
            img.seek(0)
            img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise NoEncontrado("No se pudo abrir la foto.")
    img.thumbnail((VISTA_LADO, VISTA_LADO))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()
