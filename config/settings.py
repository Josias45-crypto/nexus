import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
# Chat: solo OpenRouter (providers/openrouter.py). Una key; el nombre de la variable se conserva.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEYS", "").split(",")[0].strip()
# Modelos por variable, sin valor en el código: uso diario y potente (solo cuando el jefe lo pide)
OPENROUTER_MODEL = os.getenv("NEXUS_OPENROUTER_MODEL", "").strip()
OPENROUTER_MODEL_DEEP = os.getenv("NEXUS_OPENROUTER_MODEL_DEEP", "").strip()
# Respaldo del modelo potente: solo si está caído, limitado (429) o no existe
OPENROUTER_MODEL_DEEP_RESPALDO = os.getenv("NEXUS_OPENROUTER_MODEL_DEEP_RESPALDO", "").strip()
# Tope de gasto diario en USD (vacío = sin tope). Al alcanzarlo, el chat avisa y espera a mañana.
DAILY_BUDGET_USD = float(os.getenv("NEXUS_DAILY_BUDGET_USD", "").strip() or 0) or None
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
# /ask: cuántos candidatos recibe el modelo (él decide cuáles usar) y filtro laxo contra
# ruido evidente (distancia L2; los de término raro no se filtran).
ASK_CANDIDATES = int(os.getenv("NEXUS_ASK_CANDIDATES", "4"))
# Preguntas que piden una lista ("¿qué clientes...?", "¿cuáles...?"): más candidatos para juntar
ASK_CANDIDATES_LIST = int(os.getenv("NEXUS_ASK_CANDIDATES_LIST", "8"))
NOISE_DISTANCE = float(os.getenv("NEXUS_NOISE_DISTANCE", "1.05"))
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
# Recordatorios: insistencia fija de 20 min x 3 (core/reminders.py). Variable SOLO para pruebas:
# el e2e la acelera (admite decimales); en uso real no se define.
REMINDER_RETRY_MIN = float(os.getenv("NEXUS_REMINDER_RETRY_MIN", "20"))
REMINDER_CONFIRM_MIN = float(os.getenv("NEXUS_REMINDER_CONFIRM_MIN", "15"))  # caducidad del "¿lo guardo?"
SCHEDULER_INTERVAL = int(os.getenv("NEXUS_SCHEDULER_INTERVAL", "30"))
# Canal por defecto para avisos proactivos
DEFAULT_CHANNEL = os.getenv("NEXUS_DEFAULT_CHANNEL", "telegram")
BACKUP_DIR = os.getenv("NEXUS_BACKUP_DIR", "/backups")
BACKUP_HOURS = int(os.getenv("NEXUS_BACKUP_HOURS", "24"))
BACKUP_KEEP = int(os.getenv("NEXUS_BACKUP_KEEP", "7"))
