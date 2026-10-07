import logging

from config import settings
from providers.router import Router, build_router

log = logging.getLogger("nexus.llm")

_router: Router | None = None


def get_provider() -> Router:
    """Enrutador único del proceso (conserva el estado de las keys entre peticiones)."""
    global _router
    if _router is None:
        if settings.LLM_PROVIDER not in ("ollama", "groq"):
            raise ValueError(f"NEXUS_LLM_PROVIDER desconocido: {settings.LLM_PROVIDER} (usa ollama)")
        if settings.LLM_PROVIDER == "groq":
            log.warning(
                "NEXUS_LLM_PROVIDER=groq ya no se usa: la nube se controla con "
                "NEXUS_ALLOW_CLOUD y NEXUS_CLOUD_ORDER (docs/MODO_NUBE.md)"
            )
        _router = build_router()
    return _router
