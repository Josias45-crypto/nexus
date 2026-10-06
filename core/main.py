from fastapi import FastAPI
from pydantic import BaseModel

from config import settings
from providers.factory import get_provider

app = FastAPI(title="NEXUS")
provider = get_provider()


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "ollama_url": settings.OLLAMA_BASE_URL,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    reply = await provider.chat([{"role": "user", "content": req.message}])
    return {"reply": reply, "model": settings.LLM_MODEL}
