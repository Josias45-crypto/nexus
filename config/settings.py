import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("NEXUS_LLM_MODEL", "qwen2.5:1.5b")
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "ollama")
DATA_DIR = os.getenv("NEXUS_DATA_DIR", "/data")
EMBED_MODEL = os.getenv("NEXUS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768
