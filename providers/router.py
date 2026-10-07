"""Enrutador de modelos: decide quién responde cada petición.

- Por defecto, Ollama local (o el Ollama remoto de NEXUS_LLM_URL con respaldo local).
- La nube solo si quien llama la pide (cloud=True; hoy: /ask, /chat y digestión), el modo nube
  está encendido (NEXUS_ALLOW_CLOUD=on) y el contenido NO es privado.
- Si la nube falla, responde el local sin romperse.
"""

import logging

from config import settings
from providers.base import LLMProvider, ProviderUnavailable
from providers.cloud import CloudPool
from providers.fallback import FallbackProvider
from providers.ollama import OllamaProvider

log = logging.getLogger("nexus.llm")


class Router:
    def __init__(self, local: LLMProvider, local_model: str, pool: CloudPool | None, cloud_notes: list[str]):
        self.local = local
        self.local_model = local_model
        self.pool = pool
        self.cloud_notes = cloud_notes
        self.last: dict = {"proveedor": "ollama", "modelo": local_model, "nube": False, "key": None}

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.pool and self.pool.providers)

    async def chat_ex(
        self, messages: list[dict], *, cloud: bool = False, private: bool = False
    ) -> tuple[str, dict]:
        """Devuelve (texto, quién respondió). Nunca registra el contenido."""
        reason = None
        if cloud and self.cloud_enabled:
            if private:
                reason = "contenido privado"
            else:
                try:
                    text, info = await self.pool.chat(messages)
                    self.last = info
                    log.info("LLM respondió: %s (%s, %s)", info["proveedor"], info["modelo"], info["key"])
                    return text, info
                except ProviderUnavailable as exc:
                    reason = f"nube no disponible: {exc}"
                    log.warning("Nube no disponible (%s); respondo con el local", exc)
        text = await self.local.chat(messages)
        name = getattr(self.local, "last_name", "ollama")
        info = {"proveedor": name, "modelo": self.local_model, "nube": False, "key": None}
        if reason:
            info["motivo_local"] = reason
        self.last = info
        return text, info

    async def chat(self, messages: list[dict], *, cloud: bool = False, private: bool = False) -> str:
        return (await self.chat_ex(messages, cloud=cloud, private=private))[0]

    def status(self) -> dict:
        return {
            "ultimo": self.last,
            "nube": {
                "permitida": settings.ALLOW_CLOUD,
                "activa": self.cloud_enabled,
                "orden": [p.name for p in self.pool.providers] if self.pool else [],
                "proveedores": self.pool.status() if self.pool else {},
                "avisos": self.cloud_notes,
            },
        }


def build_router() -> Router:
    local: LLMProvider = OllamaProvider(settings.OLLAMA_BASE_URL, settings.LLM_MODEL)
    if settings.LLM_URL:
        remote = OllamaProvider(settings.LLM_URL, settings.LLM_REMOTE_MODEL, connect_timeout=3)
        local = FallbackProvider(remote, local, name="ollama-remoto")
    pool, notes = None, []
    if settings.ALLOW_CLOUD:
        pool, notes = CloudPool.from_settings()
        for n in notes:
            log.warning("Modo nube: %s", n)
        if pool.providers:
            log.warning(
                "MODO NUBE DE PRUEBAS ACTIVO (%s): preguntas y digestión no privadas pueden "
                "salir a la nube. No lo uses con datos reales.",
                ", ".join(p.name for p in pool.providers),
            )
    return Router(local, settings.LLM_MODEL, pool, notes)
