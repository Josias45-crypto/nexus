import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from core.db import connect

log = logging.getLogger("nexus.backup")

BACKUP_ROOT = Path(settings.BACKUP_DIR)
DB_DIR = BACKUP_ROOT / "db"
RAW_MIRROR = BACKUP_ROOT / "raw"
RAW_SRC = Path(settings.DATA_DIR) / "raw"

_lock = asyncio.Lock()


def _snapshots() -> list[Path]:
    return sorted(DB_DIR.glob("nexus-*.db")) if DB_DIR.exists() else []


def run_backup() -> dict:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = DB_DIR / f"nexus-{stamp}.db"

    # 1. Copia consistente de la base (segura aunque NEXUS esté escribiendo)
    with connect() as src:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()

    # 2. Verificar que la copia se puede abrir y leer
    with connect(target) as conn:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        eventos = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if not ok:
        target.unlink(missing_ok=True)
        raise RuntimeError("La copia de la base no pasó la verificación")

    # 3. Originales: copia incremental (los originales nunca cambian)
    nuevos = 0
    if RAW_SRC.exists():
        for f in RAW_SRC.rglob("*"):
            if f.is_file():
                dest = RAW_MIRROR / f.relative_to(RAW_SRC)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    nuevos += 1

    # 4. Retención: solo las últimas N copias de la base
    for old in _snapshots()[: -settings.BACKUP_KEEP]:
        old.unlink(missing_ok=True)

    return {
        "snapshot": target.name,
        "eventos_en_la_copia": eventos,
        "originales_nuevos": nuevos,
        "copias_guardadas": len(_snapshots()),
    }


async def backup_now() -> dict:
    async with _lock:
        return await asyncio.to_thread(run_backup)


def is_due() -> bool:
    snaps = _snapshots()
    if not snaps:
        return True
    age = datetime.now(timezone.utc).timestamp() - snaps[-1].stat().st_mtime
    return age >= settings.BACKUP_HOURS * 3600


def status() -> dict:
    snaps = _snapshots()
    last = (
        datetime.fromtimestamp(snaps[-1].stat().st_mtime, timezone.utc).isoformat()
        if snaps
        else None
    )
    return {
        "carpeta": str(BACKUP_ROOT),
        "cada_horas": settings.BACKUP_HOURS,
        "guarda_las_ultimas": settings.BACKUP_KEEP,
        "ultimo_respaldo": last,
        "snapshots": [s.name for s in snaps],
    }
