from abc import ABC, abstractmethod


class ProviderUnavailable(Exception):
    """El proveedor no puede responder ahora (límite de uso, caída); conviene usar el respaldo."""


class LLMProvider(ABC):
    """Contrato que debe cumplir cualquier modelo (local o API)."""

    @abstractmethod
    async def chat(self, messages: list[dict]) -> str:
        """Recibe [{"role": "user", "content": "..."}] y devuelve el texto."""
