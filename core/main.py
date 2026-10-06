from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel

from config import settings
from core import ask, inbox, processor, search, worker
from core.db import connect, init_db
from providers.factory import get_provider

app = FastAPI(title="NEXUS", lifespan=worker.lifespan)
provider = get_provider()
init_db()


class ChatRequest(BaseModel):
    message: str


class TextIn(BaseModel):
    text: str
    source: str = "api"


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


@app.post("/inbox/text")
def inbox_text(item: TextIn):
    return inbox.save(
        item.text.encode("utf-8"), "nota.txt", "text/plain", item.source, kind="text"
    )


@app.post("/inbox/file")
async def inbox_file(file: UploadFile = File(...), source: str = "api"):
    data = await file.read()
    return inbox.save(
        data,
        file.filename or "sin_nombre",
        file.content_type or "application/octet-stream",
        source,
    )


@app.get("/inbox")
def inbox_list(limit: int = 20):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, kind, source, filename, size, status, attempts, error"
            " FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/stats")
def stats():
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS total, SUM(size) AS bytes FROM events GROUP BY kind"
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) FROM events WHERE status = 'pending'"
        ).fetchone()[0]
    return {"por_tipo": [dict(r) for r in rows], "pendientes_de_digerir": pending}


@app.post("/process")
async def process(limit: int = 10):
    return await processor.process_pending(limit)


@app.get("/search")
async def search_memory(q: str, k: int = 5):
    return await search.search(q, k)


class AskRequest(BaseModel):
    question: str
    k: int = 4


@app.post("/ask")
async def ask_memory(req: AskRequest):
    return await ask.ask(req.question, req.k)


@app.get("/worker")
def worker_status():
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM events GROUP BY status"
        ).fetchall()
    return {**worker.state, "eventos_por_estado": {r["status"]: r["total"] for r in rows}}


@app.post("/requeue")
def requeue():
    with connect() as conn:
        n = conn.execute(
            "UPDATE events SET status = 'pending', attempts = 0, error = NULL"
            " WHERE status = 'failed'"
        ).rowcount
    return {"reencolados": n}
