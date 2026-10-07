import asyncio
import json
import logging
from datetime import date

import httpx

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from core import profile as nexus_profile
from core import ask, backup, brain, briefing, digest, dashboard, events, growth, inbox, outbox, processor, reindex, reminders, search, ui, worker
from core.db import connect, init_db
from core.logs import setup_logging
from providers.factory import get_provider

setup_logging()
app = FastAPI(title="NEXUS", lifespan=worker.lifespan)
provider = get_provider()
init_db()
nexus_profile.get()  # un perfil inválido detiene el arranque con un mensaje claro

MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024
TOO_LARGE = f"El archivo supera el límite de {settings.MAX_UPLOAD_MB} MB (NEXUS_MAX_UPLOAD_MB)."


log = logging.getLogger("nexus.api")


@app.exception_handler(httpx.TransportError)
async def model_unreachable(request: Request, exc: httpx.TransportError):
    log.warning("Ollama no responde en %s (%s)", request.url.path, type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "El modelo local (Ollama) no responde. Reintenta en unos segundos."},
    )


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception):
    log.exception("Error no controlado en %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno de NEXUS. Detalle en: docker logs nexus-core"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errores = [
        {"campo": ".".join(str(p) for p in e["loc"] if p != "body"), "problema": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": "Datos inválidos.", "errores": errores})


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class TextIn(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field("api", max_length=40)
    # Privado: nunca se envía a la nube (aunque el modo nube de pruebas esté activo)
    private: bool = False


async def _ollama_check() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        missing = [
            m for m in (settings.LLM_MODEL, settings.EMBED_MODEL)
            if m not in names and f"{m}:latest" not in names
        ]
        return {"ok": not missing, "modelos_faltantes": missing}
    except Exception as exc:
        return {"ok": False, "error": f"no responde ({type(exc).__name__})"}


@app.get("/health")
async def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        log.exception("La base de datos no responde")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": f"Base de datos inaccesible ({type(exc).__name__})"},
        )
    ollama = await _ollama_check()
    llm = provider.status()
    return {
        "status": "ok" if ollama["ok"] else "degradado",
        "ollama": ollama,
        "llm_model": settings.LLM_MODEL,
        "ollama_url": settings.OLLAMA_BASE_URL,
        "llm_activo": llm["ultimo"]["proveedor"],
        "nube": llm["ultimo"]["nube"],  # la última respuesta salió de la nube
        "llm": llm,
    }


@app.get("/profile")
def profile_info():
    p = nexus_profile.get()
    return {
        "id": p["id"],
        "asistente": {k: p["asistente"][k] for k in ("nombre", "idioma", "tono")},
        "negocio": p["negocio"]["descripcion"],
        "categorias": p["entidades"]["categorias"],
        "proactivo": p["proactivo"],
        "ejemplos": p["ejemplos"]["preguntas"],
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    reply, llm = await provider.chat_ex([{"role": "user", "content": req.message}], cloud=True)
    return {"reply": reply, "model": llm["modelo"], "llm": llm}


@app.post("/inbox/text")
async def inbox_text(item: TextIn):
    data = item.text.encode("utf-8")
    if not item.text.strip():
        raise HTTPException(422, "El texto está vacío.")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, TOO_LARGE)
    return await asyncio.to_thread(
        inbox.save, data, "nota.txt", "text/plain", item.source, kind="text", private=item.private
    )


@app.post("/inbox/file")
async def inbox_file(
    request: Request,
    file: UploadFile = File(...),
    source: str = Query("api", max_length=40),
    private: bool = False,
):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES + 64 * 1024:
        raise HTTPException(413, TOO_LARGE)
    try:
        return await asyncio.to_thread(
            inbox.save_stream,
            file.file,
            file.filename or "sin_nombre",
            file.content_type or "application/octet-stream",
            source,
            private=private,
        )
    except inbox.TooLarge:
        raise HTTPException(413, TOO_LARGE)
    finally:
        await file.close()


@app.get("/inbox")
def inbox_list(limit: int = Query(20, ge=1, le=500)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.created_at, e.kind, e.source, e.filename, e.size, e.status, e.attempts,"
            " e.error, e.private, d.status AS digest"
            " FROM events e LEFT JOIN digests d ON d.event_id = e.id"
            " ORDER BY e.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {**dict(r), "procesando": r["id"] in processor.current, "reintentable": r["status"] in REQUEUE_STATES}
        for r in rows
    ]


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
async def process(limit: int = Query(10, ge=1, le=100)):
    return await processor.process_pending(limit)


@app.get("/search")
async def search_memory(q: str = Query(min_length=1, max_length=2000), k: int = Query(5, ge=1, le=50)):
    return await search.search(q, k)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(4, ge=1, le=20)


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
        "llm": provider.status(),
        "digiriendo": digest.progress,
        "eventos_por_estado": {r["status"]: r["total"] for r in rows},
    }


# Estados sin trozos guardados: reprocesarlos no duplica la memoria
REQUEUE_STATES = ("failed", "unsupported", "empty")


@app.post("/requeue")
def requeue(estado: str = Query("failed", pattern="^(failed|unsupported|empty|todos)$")):
    states = REQUEUE_STATES if estado == "todos" else (estado,)
    marks = ",".join("?" * len(states))
    with connect() as conn:
        n = conn.execute(
            "UPDATE events SET status = 'pending', attempts = 0, error = NULL"
            f" WHERE status IN ({marks})",
            states,
        ).rowcount
    return {"reencolados": n}


@app.post("/requeue/digests")
def requeue_digests():
    with connect() as conn:
        n = conn.execute(
            "UPDATE digests SET status = 'retry', attempts = 0, error = NULL"
            " WHERE status = 'failed'"
        ).rowcount
    return {"reencolados": n}


@app.post("/requeue/{event_id}")
def requeue_one(event_id: str):
    with connect() as conn:
        row = conn.execute("SELECT status FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe ese evento.")
        if row["status"] not in REQUEUE_STATES:
            raise HTTPException(
                409, f"El evento está en '{row['status']}'; solo se reencola si está en "
                + ", ".join(REQUEUE_STATES) + "."
            )
        conn.execute(
            "UPDATE events SET status = 'pending', attempts = 0, error = NULL WHERE id = ?",
            (event_id,),
        )
    return {"reencolados": 1, "id": event_id}


@app.post("/reindex")
async def reindex_endpoint(event_id: str | None = None, todos: bool = False):
    """Regenera trozos, vectores y resúmenes desde los originales (uno o todos)."""
    try:
        return await reindex.reindex(event_id, todos)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.get("/knowledge")
def knowledge(limit: int = Query(20, ge=0, le=500)):
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


@app.get("/growth")
def growth_stats(days: int = Query(14, ge=1, le=366)):
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


@app.get("/today")
def today(dia: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Qué aprendió NEXUS en un día local (hoy por defecto) y sus recordatorios."""
    try:
        day = date.fromisoformat(dia) if dia else None
    except ValueError:
        raise HTTPException(422, "Fecha inválida; usa AAAA-MM-DD.")
    return briefing.today(day)


@app.get("/briefing")
async def briefing_preview():
    """Vista previa del resumen matutino (no envía nada)."""
    return await briefing.build()


@app.post("/briefing/send")
async def briefing_send(channel: str | None = Query(None, max_length=40)):
    """Envía ahora el resumen matutino al buzón del canal."""
    return await briefing.send(channel)


class ReminderText(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ReminderIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    due_at: str  # ISO con zona horaria (lo devuelve /reminders/parse)
    channel: str | None = Field(None, max_length=40)
    chat_id: str | None = Field(None, max_length=80)
    source_event_id: str | None = None


@app.post("/reminders/parse")
async def reminder_parse(req: ReminderText):
    """Interpreta sin guardar: el canal debe confirmar con el usuario y luego POST /reminders."""
    try:
        return await reminders.interpret(req.text)
    except reminders.NotUnderstood as exc:
        raise HTTPException(422, str(exc))


@app.post("/reminders")
def reminder_create(req: ReminderIn):
    try:
        return reminders.create(req.text, req.due_at, req.channel, req.chat_id, req.source_event_id)
    except ValueError as exc:
        raise HTTPException(422, f"Fecha inválida: {exc}")


@app.get("/reminders")
def reminder_list(chat_id: str | None = None, limit: int = Query(50, ge=1, le=500)):
    return reminders.list_open(chat_id, limit)


def _reminder_status(rid: int | None, status: str, chat_id: str | None):
    r = reminders.set_status(rid, status, chat_id)
    if not r:
        raise HTTPException(404, "No hay un recordatorio abierto con ese número.")
    return r


@app.post("/reminders/done")
def reminder_done_last(chat_id: str | None = None):
    return _reminder_status(None, "done", chat_id)


@app.post("/reminders/{rid}/done")
def reminder_done(rid: int):
    return _reminder_status(rid, "done", None)


@app.post("/reminders/{rid}/cancel")
def reminder_cancel(rid: int):
    return _reminder_status(rid, "cancelled", None)


@app.get("/outbox")
def outbox_pending(channel: str = Query(max_length=40), limit: int = Query(20, ge=1, le=100)):
    return outbox.pending(channel, limit)


@app.post("/outbox/{msg_id}/delivered")
def outbox_delivered(msg_id: int):
    if not outbox.delivered(msg_id):
        raise HTTPException(404, "Mensaje inexistente o ya entregado.")
    return {"ok": True}


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
