import logging
import time

import httpx

from providers.base import LLMProvider, ProviderUnavailable

log = logging.getLogger("nexus.llm")

# Si el preferido falla, no se vuelve a intentar durante este tiempo
RETRY_AFTER = 30

# Estado compartido entre instancias (get_provider() crea una por llamada)
_down_until: dict[str, float] = {}
last_used = "local"


class FallbackProvider(LLMProvider):
    """Usa el proveedor preferido (PC con GPU, nube de prueba) y cae al local si no responde."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider, name: str = "remoto"):
        self.primary = primary
        self.fallback = fallback
        self.name = name

    async def chat(self, messages: list[dict]) -> str:
        global last_used
        if time.monotonic() >= _down_until.get(self.name, 0.0):
            try:
                reply = await self.primary.chat(messages)
                last_used = self.name
                log.info("LLM respondió: %s", self.name)
                return reply
            except (httpx.TransportError, ProviderUnavailable) as exc:
                _down_until[self.name] = time.monotonic() + RETRY_AFTER
                # Solo el tipo de error: nunca el prompt ni credenciales
                log.warning(
                    "LLM %s no disponible (%s); uso el local %d s",
                    self.name,
                    type(exc).__name__,
                    RETRY_AFTER,
                )
        reply = await self.fallback.chat(messages)
        last_used = "local"
        log.info("LLM respondió: local")
        return reply
