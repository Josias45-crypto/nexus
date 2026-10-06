import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import settings

DB_PATH = Path(settings.DATA_DIR) / "nexus.db"

SCHEMA = """
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
    status TEXT NOT NULL DEFAULT 'pending'
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
