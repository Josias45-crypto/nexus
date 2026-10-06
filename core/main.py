import os

from fastapi import FastAPI

app = FastAPI(title="NEXUS")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_model": os.getenv("NEXUS_LLM_MODEL"),
        "ollama_url": os.getenv("OLLAMA_BASE_URL"),
    }
