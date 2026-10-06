from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Contrato que debe cumplir cualquier modelo (local o API)."""

    @abstractmethod
    async def chat(self, messages: list[dict]) -> str:
        """Recibe [{"role": "user", "content": "..."}] y devuelve el texto."""
