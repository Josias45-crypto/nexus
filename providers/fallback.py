import logging
import time

import httpx

from providers.base import LLMProvider, ProviderUnavailable

log = logging.getLogger("nexus.llm")

# Si el preferido falla, no se vuelve a intentar durante este tiempo
RETRY_AFTER = 30


class FallbackProvider(LLMProvider):
    """Usa el Ollama preferido (p. ej. PC con GPU) y cae al local si no responde."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider, name: str = "remoto"):
        self.primary = primary
        self.fallback = fallback
        self.name = name
        self.last_name = "ollama"
        self._down_until = 0.0

    async def chat(self, messages: list[dict]) -> str:
        if time.monotonic() >= self._down_until:
            try:
                reply = await self.primary.chat(messages)
                self.last_name = self.name
                return reply
            except (httpx.TransportError, ProviderUnavailable) as exc:
                self._down_until = time.monotonic() + RETRY_AFTER
                # Solo el tipo de error: nunca el prompt ni credenciales
                log.warning(
                    "LLM %s no disponible (%s); uso el local %d s",
                    self.name, type(exc).__name__, RETRY_AFTER,
                )
        self.last_name = "ollama"
        return await self.fallback.chat(messages)
