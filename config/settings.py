import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("NEXUS_LLM_MODEL", "qwen2.5:1.5b")
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "ollama")
# Ollama remoto preferido para el chat (vacío = solo local); los embeddings siempre van al local
LLM_URL = os.getenv("NEXUS_LLM_URL", "").strip()
LLM_REMOTE_MODEL = os.getenv("NEXUS_LLM_REMOTE_MODEL") or LLM_MODEL
# Modo nube de pruebas: APAGADO por defecto. Solo chat y digestión con datos no privados;
# los embeddings y Whisper nunca salen de la máquina. Ver docs/MODO_NUBE.md
ALLOW_CLOUD = os.getenv("NEXUS_ALLOW_CLOUD", "off").lower() == "on"
CLOUD_ORDER = [
    p.strip().lower()
    for p in os.getenv("NEXUS_CLOUD_ORDER", "groq,gemini,openrouter").split(",")
    if p.strip()
]
CLOUD_TIMEOUT = float(os.getenv("NEXUS_CLOUD_TIMEOUT", "30"))
CLOUD_MIN_COOLDOWN = int(os.getenv("NEXUS_CLOUD_COOLDOWN", "60"))
# Endpoints compatibles con OpenAI (reemplazables, p. ej. para pruebas sin red)
CLOUD_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}


def _keys(name: str) -> list[str]:
    raw = os.getenv(f"{name}_API_KEYS", "") or os.getenv(f"{name}_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


# Por proveedor: lista de keys, modelo (sin valor por defecto: lo eliges tú) y URL
CLOUD_PROVIDERS = {
    name: {
        "keys": _keys(name.upper()),
        "model": os.getenv(f"NEXUS_{name.upper()}_MODEL", "").strip(),
        "url": os.getenv(f"NEXUS_{name.upper()}_URL", "").strip() or url,
    }
    for name, url in CLOUD_URLS.items()
}
# Perfil de rubro: profiles/<NEXUS_PROFILE>.toml
PROFILE = os.getenv("NEXUS_PROFILE", "general").strip().lower()
PROFILES_DIR = os.getenv("NEXUS_PROFILES_DIR", "/app/profiles")
LOG_LEVEL = os.getenv("NEXUS_LOG_LEVEL", "INFO").upper()
DATA_DIR = os.getenv("NEXUS_DATA_DIR", "/data")
# Tamaño máximo por archivo o texto recibido (Telegram entrega a los bots hasta 20 MB)
MAX_UPLOAD_MB = int(os.getenv("NEXUS_MAX_UPLOAD_MB", "50"))
EMBED_MODEL = os.getenv("NEXUS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768
# Recalibrado con datos reales (tests/calibrate.py). Un recuerdo se usa si su distancia es
# <= MAX_DISTANCE, o <= MAX_DISTANCE + FTS_MARGIN cuando además coincide por palabras.
MAX_DISTANCE = float(os.getenv("NEXUS_MAX_DISTANCE", "0.78"))
FTS_MARGIN = float(os.getenv("NEXUS_FTS_MARGIN", "0.04"))
# Un nombre propio o cifra de la pregunta que aparece en <= N trozos es "raro": esos trozos
# entran siempre como candidatos de /ask, sin importar su distancia.
RARE_MAX_DF = int(os.getenv("NEXUS_RARE_MAX_DF", "5"))
WORKER_ENABLED = os.getenv("NEXUS_WORKER", "on").lower() == "on"
WORKER_INTERVAL = int(os.getenv("NEXUS_WORKER_INTERVAL", "20"))
MAX_ATTEMPTS = int(os.getenv("NEXUS_MAX_ATTEMPTS", "3"))
DIGEST_ENABLED = os.getenv("NEXUS_DIGEST", "on").lower() == "on"
DIGEST_MIN_CHARS = int(os.getenv("NEXUS_DIGEST_MIN_CHARS", "1200"))
# Documentos grandes: tamaño de cada sección y máximo de secciones a resumir (0 = sin límite)
DIGEST_SECTION_CHARS = int(os.getenv("NEXUS_DIGEST_SECTION_CHARS", "6000"))
DIGEST_MAX_SECTIONS = int(os.getenv("NEXUS_DIGEST_MAX_SECTIONS", "12"))
# Sentidos (senses/): todo local. OCR con Tesseract; describir fotos con un modelo de visión
# de Ollama es opcional y está apagado (vacío). Ver docs/FORMATOS.md
OCR_ENABLED = os.getenv("NEXUS_OCR", "on").lower() == "on"
OCR_LANGS = os.getenv("NEXUS_OCR_LANGS", "spa+eng")
OCR_MAX_PAGES = int(os.getenv("NEXUS_OCR_MAX_PAGES", "50"))
OCR_TIMEOUT = int(os.getenv("NEXUS_OCR_TIMEOUT", "120"))
VISION_MODEL = os.getenv("NEXUS_VISION_MODEL", "").strip()
# Comprimidos (.zip): máximo de archivos y de MB descomprimidos que se leen
ARCHIVE_MAX_FILES = int(os.getenv("NEXUS_ARCHIVE_MAX_FILES", "200"))
ARCHIVE_MAX_MB = int(os.getenv("NEXUS_ARCHIVE_MAX_MB", "200"))
# Texto máximo que se indexa por elemento (el original siempre se guarda entero)
MAX_TEXT_CHARS = int(os.getenv("NEXUS_MAX_TEXT_CHARS", "2000000"))
WHISPER_MODEL =os.getenv("NEXUS_WHISPER_MODEL", "small")
WHISPER_LANG = os.getenv("NEXUS_WHISPER_LANG", "es")
WHISPER_THREADS = int(os.getenv("NEXUS_WHISPER_THREADS", "4"))
WHISPER_DIR = os.getenv("NEXUS_WHISPER_DIR", "/models/whisper")
TZ_OFFSET = int(os.getenv("NEXUS_TZ_OFFSET", "-5"))
# Recordatorios: insistir cada N minutos (admite decimales) hasta M avisos
REMINDER_RETRY_MIN = float(os.getenv("NEXUS_REMINDER_RETRY_MIN", "30"))
REMINDER_MAX_TRIES = int(os.getenv("NEXUS_REMINDER_MAX_TRIES", "3"))
SCHEDULER_INTERVAL = int(os.getenv("NEXUS_SCHEDULER_INTERVAL", "30"))
# Canal por defecto para avisos proactivos
DEFAULT_CHANNEL = os.getenv("NEXUS_DEFAULT_CHANNEL", "telegram")
BACKUP_DIR = os.getenv("NEXUS_BACKUP_DIR", "/backups")
BACKUP_HOURS = int(os.getenv("NEXUS_BACKUP_HOURS", "24"))
BACKUP_KEEP = int(os.getenv("NEXUS_BACKUP_KEEP", "7"))
