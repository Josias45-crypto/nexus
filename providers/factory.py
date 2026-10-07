from config import settings
from providers.base import LLMProvider
from providers.fallback import FallbackProvider
from providers.ollama import OllamaProvider


def get_provider() -> LLMProvider:
    if settings.LLM_PROVIDER == "ollama":
        local = OllamaProvider(settings.OLLAMA_BASE_URL, settings.LLM_MODEL)
        if settings.LLM_URL:
            remote = OllamaProvider(settings.LLM_URL, settings.LLM_REMOTE_MODEL, connect_timeout=3)
            return FallbackProvider(remote, local)
        return local
    raise ValueError(f"Proveedor desconocido: {settings.LLM_PROVIDER}")
