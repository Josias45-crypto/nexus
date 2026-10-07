import sqlite3
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from config import settings

DB_PATH = Path(settings.DATA_DIR) / "nexus.db"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    filename TEXT,
    mime TEXT,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    raw_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    position INTEGER NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_event ON chunks(event_id);

CREATE TABLE IF NOT EXISTS digests (
    event_id TEXT PRIMARY KEY REFERENCES events(id),
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    concepts TEXT,
    model TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    embedding float[{settings.EMBED_DIM}]
);
"""


BUSY_TIMEOUT_MS = 30000  # espera a que otro escritor termine en vez de fallar con "locked"


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous = NORMAL")  # seguro con WAL y más rápido
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Agrega columnas nuevas a bases creadas por versiones anteriores."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "attempts" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "error" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN error TEXT")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        # WAL: los lectores no bloquean al escritor (worker, API y respaldos a la vez).
        # Es persistente: queda guardado en el archivo de la base.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
