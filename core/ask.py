from config import settings
from core.search import search
from providers.factory import get_provider

NO_INFO = "No tengo información sobre eso en mi memoria."

SYSTEM = (
    "Eres NEXUS, la memoria personal de tu usuario. Responde usando ÚNICAMENTE "
    "las fuentes numeradas que se te entregan. Cita la fuente con [n] después de "
    "cada dato. Si las fuentes no contienen la respuesta, responde exactamente: "
    f'"{NO_INFO}" No inventes nada.'
)


async def ask(question: str, k: int = 4) -> dict:
    hits = await search(question, k)
    hits = [
        h for h in hits if h["distance"] is None or h["distance"] <= settings.MAX_DISTANCE
    ]
    if not hits:
        return {"answer": NO_INFO, "sources": []}

    context = "\n\n".join(
        f"[{i}] ({h['filename']})\n{h['content']}" for i, h in enumerate(hits, 1)
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Fuentes:\n{context}\n\nPregunta: {question}"},
    ]
    answer = await get_provider().chat(messages)
    sources = [
        {
            "n": i,
            "event_id": h["event_id"],
            "filename": h["filename"],
            "position": h["position"],
            "distance": h["distance"],
            "snippet": h["content"][:200],
        }
        for i, h in enumerate(hits, 1)
    ]
    return {"answer": answer, "sources": sources}
