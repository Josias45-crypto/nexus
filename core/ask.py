import re

from config import settings
from core import citations, profile, verify
from core.search import publish_recall, search
from providers.factory import get_provider

NO_INFO = "No tengo información sobre eso en mi memoria."

RULES = (
    "Responde la pregunta usando solo la información de las fuentes. Algunas fuentes pueden no "
    "tener relación con la pregunta: ignóralas. Si la pregunta pide varios datos o una lista "
    "(por ejemplo, qué clientes hicieron algo), revisa todas las fuentes y junta todos los que "
    "correspondan. Incluye los nombres y cifras exactos de las fuentes. Responde en español, "
    "con frases completas y breves. "
    f'Si ninguna fuente contiene la respuesta, responde exactamente: "{NO_INFO}"'
)


def candidates(hits: list[dict]) -> list[dict]:
    """Candidatos para el modelo: los de término raro siempre; el resto, salvo ruido evidente."""
    rare = [h for h in hits if h.get("termino_raro")]
    rest = [
        h for h in hits
        if not h.get("termino_raro")
        and h["distance"] is not None and h["distance"] <= settings.NOISE_DISTANCE
    ]
    return (rare + rest)[: settings.ASK_CANDIDATES]


def _says_no(answer: str) -> bool:
    return NO_INFO.rstrip(".").lower() in answer.lower()


def _is_empty_answer(answer: str) -> bool:
    """Los modelos pequeños a veces responden solo '[1]'. Eso no es una respuesta."""
    return len(re.sub(r"\[\d+\]", "", answer).strip(" .:-\n")) < 10


async def ask(question: str, k: int | None = None) -> dict:
    from core import questions

    special = await questions.route(question)
    if special is not None:
        return special
    hits = candidates(await search(question, k or settings.ASK_CANDIDATES, publish=False))
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

    # Anti-invención: cada nombre propio y cifra debe estar en las fuentes (o en la pregunta)
    texts = [h["content"] for h in hits] + [citations.cite(h) for h in hits]
    texts.append(profile.get()["asistente"]["nombre"])
    missing = [] if _says_no(answer) else verify.unverified(answer, texts, question)
    if missing:
        retry = messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": (
                f"Estos datos no aparecen en las fuentes: {', '.join(missing)}. Vuelve a responder "
                "usando solo nombres y cifras que estén escritos en las fuentes."
            )},
        ]
        answer, llm = await get_provider().chat_ex(retry, cloud=True, private=private)
        answer = citations.strip_markers(answer)
        missing = [] if _says_no(answer) else verify.unverified(answer, texts, question)
        llm = {**llm, "regenerada": True}
        if missing:
            answer = f"{answer}\n\n⚠ No pude verificar: {', '.join(missing)}"
    if _says_no(answer):
        # Dijo que no sabe: mostrar fuentes sería engañoso
        return {"answer": NO_INFO, "sources": [], "llm": llm, "tipo": "memoria"}
    if _is_empty_answer(answer):
        answer = "Esto es lo que encontré en mi memoria:\n" + hits[0]["content"][:500]
        backing = hits[:1]
    else:
        backing = citations.supporting(answer, hits)
    return {
        "answer": answer, "sources": sources_for(backing), "llm": llm, "tipo": "memoria",
        "sin_verificar": missing,
    }


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
