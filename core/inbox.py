import hashlib
import io
import os
import re
import sqlite3
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from config import settings
from core import events
from core.db import connect

RAW_DIR = Path(settings.DATA_DIR) / "raw"
TMP_DIR = Path(settings.DATA_DIR) / "tmp"  # mismo disco que raw/: mover es instantáneo
COPY_BLOCK = 1024 * 1024
MAX_NAME_BYTES = 120  # deja margen al límite de 255 bytes con el prefijo del id


class TooLarge(Exception):
    """El archivo supera NEXUS_MAX_UPLOAD_MB."""


def _kind(mime: str, filename: str = "") -> str:
    from senses import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT

    ext = Path(filename).suffix.lower()
    if ext in AUDIO_EXT or (mime.startswith("audio/") and ext not in VIDEO_EXT):
        return "audio"
    if ext in VIDEO_EXT or mime.startswith("video/"):
        return "video"
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "image"
    return "document"


def safe_filename(filename: str) -> str:
    """Nombre seguro para el disco: sin rutas ni caracteres de control y de largo acotado."""
    name = unicodedata.normalize("NFC", (filename or "").replace("\\", "/")).split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip(" .")
    if not name:
        return "sin_nombre"
    if len(name.encode()) <= MAX_NAME_BYTES:
        return name
    stem, ext = os.path.splitext(name)
    ext = ext if len(ext.encode()) <= 16 else ""
    budget = MAX_NAME_BYTES - len(ext.encode())
    stem = stem.encode()[:budget].decode(errors="ignore").rstrip()
    return (stem or "sin_nombre") + ext


def _spool(src: BinaryIO) -> tuple[Path, str, int]:
    """Copia a un temporal por bloques, calculando el hash y cortando si excede el límite."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    sha, size = hashlib.sha256(), 0
    fd, name = tempfile.mkstemp(dir=TMP_DIR)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            while block := src.read(COPY_BLOCK):
                size += len(block)
                if size > limit:
                    raise TooLarge()
                sha.update(block)
                out.write(block)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, sha.hexdigest(), size


def _existing(conn, sha: str) -> dict | None:
    row = conn.execute("SELECT id, kind FROM events WHERE sha256 = ?", (sha,)).fetchone()
    return {"id": row["id"], "kind": row["kind"], "duplicate": True} if row else None


def save_stream(
    src: BinaryIO,
    filename: str,
    mime: str,
    source: str,
    kind: str | None = None,
    private: bool = False,
) -> dict:
    """Guarda el original intacto en data/raw y registra el evento. Es bloqueante:
    desde código async llamarla con asyncio.to_thread."""
    tmp, sha, size = _spool(src)
    try:
        with connect() as conn:
            dup = _existing(conn, sha)
        if dup:
            return dup

        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4().hex
        safe_name = safe_filename(filename)
        folder = RAW_DIR / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{event_id}_{safe_name}"
        os.replace(tmp, path)

        kind = kind or _kind(mime, safe_name)
        try:
            with connect() as conn:
                conn.execute(
                    "INSERT INTO events (id, created_at, kind, source, filename, mime, size,"
                    " sha256, raw_path, private) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id, now.isoformat(), kind, source, safe_name, mime, size,
                        sha, str(path), int(private),
                    ),
                )
        except sqlite3.IntegrityError:
            # Otra subida idéntica ganó la carrera: se conserva la suya
            path.unlink(missing_ok=True)
            with connect() as conn:
                dup = _existing(conn, sha)
            if dup:
                return dup
            raise
    finally:
        tmp.unlink(missing_ok=True)

    events.publish("ingest", {"id": event_id, "kind": kind, "filename": safe_name})
    return {"id": event_id, "kind": kind, "size": size, "private": private, "duplicate": False}


def save(
    data: bytes,
    filename: str,
    mime: str,
    source: str,
    kind: str | None = None,
    private: bool = False,
) -> dict:
    return save_stream(io.BytesIO(data), filename, mime, source, kind, private)
