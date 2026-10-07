import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("NEXUS_LLM_MODEL", "qwen2.5:1.5b")
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "ollama")
# Ollama remoto preferido para el chat (vacío = solo local); los embeddings siempre van al local
LLM_URL = os.getenv("NEXUS_LLM_URL", "").strip()
LLM_REMOTE_MODEL = os.getenv("NEXUS_LLM_REMOTE_MODEL") or LLM_MODEL
# Nube SOLO para pruebas (NEXUS_LLM_PROVIDER=groq). Apagada por defecto; nunca embeddings ni audio
ALLOW_CLOUD = os.getenv("NEXUS_ALLOW_CLOUD", "off").lower() == "on"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("NEXUS_GROQ_MODEL", "").strip()
# Perfil de rubro: profiles/<NEXUS_PROFILE>.toml
PROFILE = os.getenv("NEXUS_PROFILE", "general").strip().lower()
PROFILES_DIR = os.getenv("NEXUS_PROFILES_DIR", "/app/profiles")
LOG_LEVEL = os.getenv("NEXUS_LOG_LEVEL", "INFO").upper()
DATA_DIR = os.getenv("NEXUS_DATA_DIR", "/data")
# Tamaño máximo por archivo o texto recibido (Telegram entrega a los bots hasta 20 MB)
MAX_UPLOAD_MB = int(os.getenv("NEXUS_MAX_UPLOAD_MB", "50"))
EMBED_MODEL = os.getenv("NEXUS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768
MAX_DISTANCE = float(os.getenv("NEXUS_MAX_DISTANCE", "0.8"))
WORKER_ENABLED = os.getenv("NEXUS_WORKER", "on").lower() == "on"
WORKER_INTERVAL = int(os.getenv("NEXUS_WORKER_INTERVAL", "20"))
MAX_ATTEMPTS = int(os.getenv("NEXUS_MAX_ATTEMPTS", "3"))
DIGEST_ENABLED = os.getenv("NEXUS_DIGEST", "on").lower() == "on"
DIGEST_MIN_CHARS = int(os.getenv("NEXUS_DIGEST_MIN_CHARS", "1200"))
WHISPER_MODEL = os.getenv("NEXUS_WHISPER_MODEL", "small")
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
