import logging

from config import settings
from providers.base import LLMProvider
from providers.fallback import FallbackProvider
from providers.ollama import OllamaProvider

log = logging.getLogger("nexus.llm")

# Motivo por el que no se pudo activar la nube (se muestra en /health)
cloud_error: str | None = None


def _groq_blocker() -> str | None:
    if not settings.ALLOW_CLOUD:
        return "NEXUS_LLM_PROVIDER=groq requiere NEXUS_ALLOW_CLOUD=on (la nube está apagada)"
    if not settings.GROQ_API_KEY:
        return "NEXUS_LLM_PROVIDER=groq requiere GROQ_API_KEY en .env"
    if not settings.GROQ_MODEL:
        return "NEXUS_LLM_PROVIDER=groq requiere NEXUS_GROQ_MODEL en .env"
    return None


def get_provider() -> LLMProvider:
    global cloud_error
    local = OllamaProvider(settings.OLLAMA_BASE_URL, settings.LLM_MODEL)
    if settings.LLM_PROVIDER == "groq":
        blocker = _groq_blocker()
        if blocker:
            if cloud_error != blocker:
                log.error("Nube desactivada: %s. Uso el Ollama local.", blocker)
            cloud_error = blocker
            return local
        cloud_error = None
        from providers.groq import GroqProvider

        groq = GroqProvider(settings.GROQ_API_KEY, settings.GROQ_MODEL)
        return FallbackProvider(groq, local, name="groq")
    if settings.LLM_PROVIDER == "ollama":
        if settings.LLM_URL:
            remote = OllamaProvider(settings.LLM_URL, settings.LLM_REMOTE_MODEL, connect_timeout=3)
            return FallbackProvider(remote, local)
        return local
    raise ValueError(f"Proveedor desconocido: {settings.LLM_PROVIDER}")
