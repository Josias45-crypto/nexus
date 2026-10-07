import httpx

from providers.base import LLMProvider, ProviderUnavailable

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    """Groq (API compatible con OpenAI). Solo para pruebas: los datos salen a la nube."""

    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self.model = model

    async def chat(self, messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "messages": messages},
            )
        # Límite de uso o caída del servicio: que actúe el respaldo local
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"Groq respondió HTTP {r.status_code}")
        if r.status_code >= 400:
            # Sin el cuerpo: podría repetir parte del prompt
            raise RuntimeError(f"Groq rechazó la petición: HTTP {r.status_code}")
        return r.json()["choices"][0]["message"]["content"]
