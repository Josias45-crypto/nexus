import httpx

from providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, connect_timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(300, connect=connect_timeout)

    async def chat(self, messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
