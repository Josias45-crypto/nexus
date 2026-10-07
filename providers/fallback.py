import logging
import time

import httpx

from providers.base import LLMProvider

log = logging.getLogger("nexus.llm")

# Si el remoto falla, no se vuelve a intentar durante este tiempo
RETRY_AFTER = 30

# Estado compartido entre instancias (get_provider() crea una por llamada)
_remote_down_until = 0.0
last_used = "local"


class FallbackProvider(LLMProvider):
    """Usa el modelo remoto (PC con GPU) y cae al local si no responde."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider):
        self.primary = primary
        self.fallback = fallback

    async def chat(self, messages: list[dict]) -> str:
        global _remote_down_until, last_used
        if time.monotonic() >= _remote_down_until:
            try:
                reply = await self.primary.chat(messages)
                last_used = "remoto"
                log.info("LLM respondió: remoto")
                return reply
            except httpx.TransportError as exc:
                _remote_down_until = time.monotonic() + RETRY_AFTER
                log.warning(
                    "LLM remoto no disponible (%s); uso el local %d s",
                    type(exc).__name__,
                    RETRY_AFTER,
                )
        reply = await self.fallback.chat(messages)
        last_used = "local"
        log.info("LLM respondió: local")
        return reply
