import re

from core import citations, profile
from core.search import publish_recall, relevant, search
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
    from core import questions

    special = await questions.route(question)
    if special is not None:
        return special
    hits = await search(question, k, publish=False)
    hits = relevant(hits)
    if not hits:
        return {"answer": NO_INFO, "sources": [], "llm": None, "tipo": "memoria"}
    publish_recall("ask", hits)

    context = "\n\n".join(
        f"Fuente {i} ({citations.cite(h)}):\n{h['content']}" for i, h in enumerate(hits, 1)
    )
    messages = [
        {"role": "system", "content": f"{profile.system_prompt()} {RULES}"},
        {"role": "user", "content": f"{context}\n\nPregunta: {question}\nRespuesta:"},
    ]
    # Si algún trozo usado es privado, responde solo el modelo local
    private = any(h.get("private") for h in hits)
    answer, llm = await get_provider().chat_ex(messages, cloud=True, private=private)
    answer = citations.strip_markers(answer)
    if NO_INFO.rstrip(".").lower() in answer.lower():
        # Dijo que no sabe: mostrar fuentes sería engañoso
        return {"answer": NO_INFO, "sources": [], "llm": llm, "tipo": "memoria"}
    if _is_empty_answer(answer):
        answer = "Esto es lo que encontré en mi memoria:\n" + hits[0]["content"][:500]
        backing = hits[:1]
    else:
        backing = citations.supporting(answer, hits)
    return {"answer": answer, "sources": sources_for(backing), "llm": llm, "tipo": "memoria"}


def sources_for(hits: list[dict]) -> list[dict]:
    return [
        {
            "n": i,
            "event_id": h["event_id"],
            "filename": h["filename"],
            "cita": citations.cite(h),
            "ubicacion": citations.location(h.get("meta") or {}),
            "position": h["position"],
            "distance": h["distance"],
            "snippet": h["content"][:200],
        }
        for i, h in enumerate(hits, 1)
    ]
