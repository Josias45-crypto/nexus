from config import settings
from providers.base import LLMProvider
from providers.ollama import OllamaProvider


def get_provider() -> LLMProvider:
    if settings.LLM_PROVIDER == "ollama":
        return OllamaProvider(settings.OLLAMA_BASE_URL, settings.LLM_MODEL)
    raise ValueError(f"Proveedor desconocido: {settings.LLM_PROVIDER}")
