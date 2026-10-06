import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("NEXUS_LLM_MODEL", "qwen2.5:1.5b")
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "ollama")
DATA_DIR = os.getenv("NEXUS_DATA_DIR", "/data")
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
