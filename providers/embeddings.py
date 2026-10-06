import httpx

from config import settings


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, batch_size: int = 16):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=300) as client:
            for i in range(0, len(inputs), self.batch_size):
                batch = inputs[i : i + self.batch_size]
                r = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch},
                )
                r.raise_for_status()
                out.extend(r.json()["embeddings"])
        return out

    # nomic-embed-text rinde mejor con estos prefijos
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([f"search_document: {t}" for t in texts])

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([f"search_query: {text}"]))[0]


def get_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(settings.OLLAMA_BASE_URL, settings.EMBED_MODEL)
