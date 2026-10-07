import asyncio
import json

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from core import ask, backup, brain, dashboard, events, growth, inbox, processor, search, ui, worker
from core.db import connect, init_db
from providers import factory, fallback
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
        "llm_activo": fallback.last_used,
        "nube": fallback.last_used == "groq",
        "nube_error": factory.cloud_error,
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
    return {
        **worker.state,
        "llm_activo": fallback.last_used,
        "nube": fallback.last_used == "groq",
        "eventos_por_estado": {r["status"]: r["total"] for r in rows},
    }


@app.post("/requeue")
def requeue():
    with connect() as conn:
        n = conn.execute(
            "UPDATE events SET status = 'pending', attempts = 0, error = NULL"
            " WHERE status = 'failed'"
        ).rowcount
    return {"reencolados": n}


@app.get("/knowledge")
def knowledge(limit: int = 20):
    with connect() as conn:
        counts = conn.execute(
            "SELECT status, COUNT(*) AS total FROM digests GROUP BY status"
        ).fetchall()
        rows = conn.execute(
            "SELECT e.id AS event_id, e.filename, d.summary, d.concepts, d.model, d.created_at"
            " FROM digests d JOIN events e ON e.id = d.event_id"
            " WHERE d.status = 'done' ORDER BY d.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "por_estado": {r["status"]: r["total"] for r in counts},
        "resumenes": [{**dict(r), "concepts": json.loads(r["concepts"] or "[]")} for r in rows],
    }


@app.post("/requeue/digests")
def requeue_digests():
    with connect() as conn:
        n = conn.execute(
            "UPDATE digests SET status = 'retry', attempts = 0, error = NULL"
            " WHERE status = 'failed'"
        ).rowcount
    return {"reencolados": n}


@app.get("/growth")
def growth_stats(days: int = 14):
    return growth.growth(days)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return dashboard.render(growth.growth(14), backup.status())


@app.post("/backup")
async def run_backup_endpoint():
    result = await backup.backup_now()
    if isinstance(result, dict):
        events.publish("backup", {"eventos": result.get("eventos_en_la_copia")})
    return result


@app.get("/backup")
def backup_status():
    return backup.status()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return ui.PAGE


@app.get("/brain", response_class=HTMLResponse, include_in_schema=False)
def brain_page():
    return brain.PAGE


@app.get("/brain/graph")
def brain_graph():
    return brain.graph()


KEEPALIVE_SECONDS = 15


@app.get("/brain/stream")
async def brain_stream():
    async def stream():
        q = events.subscribe()
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
