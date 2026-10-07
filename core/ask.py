import re

from config import settings
from core import profile
from core.search import publish_recall, search
from providers.factory import get_provider

NO_INFO = "No tengo información sobre eso en mi memoria."

RULES = (
    "Responde la pregunta usando solo la información de las fuentes. Responde con una o dos "
    "frases completas en español. "
    f'Si las fuentes no contienen la respuesta, responde exactamente: "{NO_INFO}"'
)


def _is_empty_answer(answer: str) -> bool:
    """Los modelos pequeños a veces responden solo '[1]'. Eso no es una respuesta."""
    return len(re.sub(r"\[\d+\]", "", answer).strip(" .:-\n")) < 10


async def ask(question: str, k: int = 4) -> dict:
    hits = await search(question, k, publish=False)
    hits = [
        h for h in hits if h["distance"] is None or h["distance"] <= settings.MAX_DISTANCE
    ]
    if not hits:
        return {"answer": NO_INFO, "sources": [], "llm": None}
    publish_recall("ask", hits)

    context = "\n\n".join(
        f"Fuente {i} ({h['filename']}):\n{h['content']}" for i, h in enumerate(hits, 1)
    )
    messages = [
        {"role": "system", "content": f"{profile.system_prompt()} {RULES}"},
        {"role": "user", "content": f"{context}\n\nPregunta: {question}\nRespuesta:"},
    ]
    # Si algún trozo usado es privado, responde solo el modelo local
    private = any(h.get("private") for h in hits)
    answer, llm = await get_provider().chat_ex(messages, cloud=True, private=private)
    if _is_empty_answer(answer):
        answer = "Esto es lo que encontré en mi memoria:\n" + hits[0]["content"][:500]

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
    return {"answer": answer, "sources": sources, "llm": llm}
