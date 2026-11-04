import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from config import settings

DB_PATH = Path(settings.DATA_DIR) / "nexus.db"
# Vectores: una tabla por modelo y dimensión (vec_baai_bge_m3_1024). Cambiar de modelo crea una tabla
# nueva y la vieja queda hasta comprobar la nueva (POST /reindex?solo_vectores=true la llena).
VEC = f"vec_{re.sub(r'[^a-z0-9]+', '_', settings.EMBED_MODEL.lower()).strip('_') or 'sin_modelo'}_{settings.EMBED_DIM}"
VEC_VIEJA = "vec_chunks"  # la de nomic-embed-text (768) por Ollama, hasta la Fase 4

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
    error TEXT,
    private INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    meta TEXT  -- JSON: página, sección, hoja, marca de tiempo... (para citar)
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

-- Recordatorios: due_at en UTC (ISO). status: pending, sent, done, cancelled
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    channel TEXT,
    chat_id TEXT,
    source_event_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_sent_at TEXT,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status, due_at);

-- Recordatorio entendido que espera el "sí" del usuario (uno por chat). Vive en la base para
-- sobrevivir a reinicios del canal; caduca en expires_at (UTC, ISO). reminder_id se llena al
-- confirmar: un "sí" repetido devuelve ese mismo recordatorio en vez de crear otro.
CREATE TABLE IF NOT EXISTS pending_confirmations (
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'reminder',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reminder_id INTEGER,
    PRIMARY KEY (channel, chat_id)
);

-- Buzón de salida: lo que NEXUS quiere decir; cada canal lo recoge y confirma la entrega.
-- chat_id NULL = a todos los dueños autorizados de ese canal.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    chat_id TEXT,
    kind TEXT NOT NULL,
    ref_id TEXT,
    text TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(channel, delivered_at);

-- Pequeño almacén clave-valor (p. ej. día del último resumen matutino)
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Uso del modelo de chat por petición (tokens y costo en USD si el proveedor lo informa)
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    day TEXT NOT NULL,              -- día local (NEXUS_TZ_OFFSET)
    provider TEXT NOT NULL,
    model TEXT,
    cloud INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage(day);

-- Conversación con el jefe (core/charla.py): historial completo; rol jefe o isabelle
CREATE TABLE IF NOT EXISTS charla (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    chat_id TEXT,
    rol TEXT NOT NULL,
    texto TEXT NOT NULL,
    accion TEXT
);
CREATE INDEX IF NOT EXISTS idx_charla_fecha ON charla(created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS charla_fts USING fts5(
    texto, content='charla', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS charla_ai AFTER INSERT ON charla BEGIN
    INSERT INTO charla_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS charla_ad AFTER DELETE ON charla BEGIN
    INSERT INTO charla_fts(charla_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
END;

-- Iniciativa propia (core/iniciativa.py): un saludo por día y tipo (manana o noche), nunca dos.
-- estado: redactando | enviado | saltado (motivo: tarde, conversando, despedida, preferencia...)
CREATE TABLE IF NOT EXISTS iniciativa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia TEXT NOT NULL,              -- día local AAAA-MM-DD
    tipo TEXT NOT NULL,
    estado TEXT NOT NULL,
    motivo TEXT,
    hora TEXT,                      -- hora local programada HH:MM
    texto TEXT,
    fuente TEXT,                    -- ia | respaldo | pregunta
    detalle TEXT,                   -- por qué se usó el respaldo
    respondido INTEGER NOT NULL DEFAULT 0,
    dias_sin_respuesta INTEGER NOT NULL DEFAULT 0,
    creado_at TEXT NOT NULL,
    actualizado_at TEXT NOT NULL,
    enviado_at TEXT,
    UNIQUE (dia, tipo)
);

-- Búsquedas en internet (core/internet.py): para el tope diario y el costo; nunca la consulta
CREATE TABLE IF NOT EXISTS busquedas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    dia TEXT NOT NULL,              -- día local
    fuentes INTEGER NOT NULL DEFAULT 0,
    costo_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_busquedas_dia ON busquedas(dia);

-- «Olvida eso» (core/olvido.py): qué se borró y cuándo, SIN contenido
CREATE TABLE IF NOT EXISTS olvidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    tipo TEXT NOT NULL              -- nota | archivo | ficha | charla | recordatorio | resumen
);

-- Sesiones de la web (core/auth.py): solo la huella (sha256) del token de la cookie
CREATE TABLE IF NOT EXISTS sesiones (
    huella TEXT PRIMARY KEY,
    creada TEXT NOT NULL,
    expira TEXT NOT NULL
);

-- Caché de embeddings por huella (sha256 del modelo y el texto): un texto repetido no se vuelve a
-- pedir a OpenRouter. Es derivado: se puede borrar sin perder nada.
CREATE TABLE IF NOT EXISTS embed_cache (
    huella TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);

-- Ver y oír (core/medios.py): el análisis de cada foto, PDF escaneado, audio o video, hecho UNA
-- vez y ligado a su original (events.sha256 es la huella). Si llega repetido se reutiliza.
-- estado: analizado | dudoso (audio que no se entendió) | guardado (video, sin analizar)
CREATE TABLE IF NOT EXISTS medios (
    event_id TEXT PRIMARY KEY REFERENCES events(id),
    tipo TEXT NOT NULL,             -- foto | pdf | audio | video
    estado TEXT NOT NULL,
    clase TEXT,                     -- de la foto: foto | documento | boleta | factura | captura | otro
    descripcion TEXT,
    texto TEXT,                     -- texto legible de la foto o transcripción del audio
    datos TEXT,                     -- JSON: lista de tipo, valor y qué es (solo los que están en el texto)
    ilegible TEXT,
    partes TEXT,                    -- JSON. PDF: texto por página escaneada leída; audio: ventanas con inicio, fin y texto
    paginas_total INTEGER,          -- PDF: páginas escaneadas en total
    duracion_s REAL,                -- audio: duración total
    transcrito_s REAL,              -- audio: segundos transcritos (tope [medios].audio_max_min)
    notas TEXT,                     -- JSON: lo que el jefe dijo de este medio («se llama Max»)
    confirmado INTEGER NOT NULL DEFAULT 0,  -- el jefe confirmó las cifras leídas
    modelo TEXT,
    costo_usd REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS {VEC} USING vec0(
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
    if "private" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN private INTEGER NOT NULL DEFAULT 0")
    chunk_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    if "meta" not in chunk_cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN meta TEXT")
    _migrate_reminders(conn)
    outbox_cols = {r["name"] for r in conn.execute("PRAGMA table_info(outbox)")}
    if "botones" not in outbox_cols:
        conn.execute("ALTER TABLE outbox ADD COLUMN botones TEXT")  # JSON: filas de {texto, dato}
    # Entrega con acuse (message_id de Telegram) y reintentos con espera creciente
    for col, tipo in (("acuse", "TEXT"), ("intentos", "INTEGER NOT NULL DEFAULT 0"), ("proximo_intento", "TEXT"),
                      ("ultimo_error", "TEXT")):
        if col not in outbox_cols:
            conn.execute(f"ALTER TABLE outbox ADD COLUMN {col} {tipo}")


def _migrate_reminders(conn) -> None:
    """Fase 2C: máquina de estados (core/reminders.py). Solo agrega columnas y las llena desde
    status; no borra nada (status queda como columna antigua sin uso)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
    nuevas = {
        "estado": "TEXT", "proximo_envio": "TEXT", "insistencias": "INTEGER NOT NULL DEFAULT 0",
        "regla": "TEXT", "serie_id": "INTEGER", "anticipacion_min": "INTEGER", "ultima_respuesta": "TEXT",
    }
    for col, tipo in nuevas.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {tipo}")
    if "estado" not in cols:
        retry = f"+{settings.REMINDER_RETRY_MIN} minutes"
        conn.execute(
            "UPDATE reminders SET"
            " estado = CASE status WHEN 'pending' THEN 'creado' WHEN 'sent' THEN"
            "   CASE WHEN attempts >= 4 THEN 'sin_confirmar' ELSE 'esperando' END"
            "   WHEN 'done' THEN 'hecho' ELSE 'cancelado' END,"
            " insistencias = CASE WHEN status = 'sent' THEN MAX(attempts - 1, 0) ELSE 0 END,"
            " proximo_envio = CASE WHEN status = 'pending' THEN due_at"
            "   WHEN status = 'sent' AND attempts < 4 THEN strftime('%Y-%m-%dT%H:%M:%S+00:00', last_sent_at, ?)"
            "   ELSE NULL END",
            (retry,),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_estado ON reminders(estado, proximo_envio)")


def init_db() -> None:
    if not settings.EMBED_MODEL or settings.EMBED_DIM <= 0:
        raise RuntimeError("Falta NEXUS_EMBED_MODEL o NEXUS_EMBED_DIM en .env (modelo de embeddings de OpenRouter).")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        # WAL: los lectores no bloquean al escritor (worker, API y respaldos a la vez).
        # Es persistente: queda guardado en el archivo de la base.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
