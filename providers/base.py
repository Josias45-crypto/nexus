from abc import ABC, abstractmethod


class ProviderUnavailable(Exception):
    """El proveedor no puede responder ahora (límite de uso, caída); conviene usar el respaldo."""

    def __init__(self, reason: str, user_message: str | None = None):
        super().__init__(reason)
        self.user_message = user_message or reason


class ChatUnavailable(Exception):
    """Nadie puede responder sin romper una regla (respaldo apagado, privado sin modelo local).
    El texto es el aviso que se muestra al dueño."""


class LLMProvider(ABC):
    """Contrato que debe cumplir cualquier modelo (local o API)."""

    @abstractmethod
    async def chat(self, messages: list[dict]) -> str:
        """Recibe [{"role": "user", "content": "..."}] y devuelve el texto."""
