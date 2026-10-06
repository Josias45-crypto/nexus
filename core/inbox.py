import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from core.db import connect

RAW_DIR = Path(settings.DATA_DIR) / "raw"


def _kind(mime: str, filename: str = "") -> str:
    if mime.startswith("audio/") or Path(filename).suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac"}:
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "document"


def save(data: bytes, filename: str, mime: str, source: str, kind: str | None = None) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, kind FROM events WHERE sha256 = ?", (sha,)
        ).fetchone()
        if row:
            return {"id": row["id"], "kind": row["kind"], "duplicate": True}

        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4().hex
        safe_name = Path(filename).name or "sin_nombre"
        folder = RAW_DIR / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{event_id}_{safe_name}"
        path.write_bytes(data)

        kind = kind or _kind(mime, safe_name)
        conn.execute(
            "INSERT INTO events (id, created_at, kind, source, filename, mime, size, sha256, raw_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, now.isoformat(), kind, source, safe_name, mime, len(data), sha, str(path)),
        )
    return {"id": event_id, "kind": kind, "size": len(data), "duplicate": False}
