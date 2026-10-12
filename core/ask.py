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
    "Si la pregunta nombra a una persona, empresa, producto, código o proyecto, no uses fuentes "
    "que hablen de otro nombre parecido o de otro proyecto (si pregunta por Rosaura, no "
    "respondas con datos de Rosario). "
    f'Si ninguna fuente contiene la respuesta, responde exactamente: "{NO_INFO}"'
)


# Preguntas que piden una lista o juntar varias notas: reciben más candidatos
RE_LIST = re.compile(
    r"\b(qu[eé]\s+(clientes|productos|pedidos|proveedores|personas|cosas)|cu[aá]les|qui[eé]nes"
    r"|todos|todas|lista|qu[eé]\s+hay|a\s+qu[eé]\s+clientes)\b",
    re.I,
)


def wants_list(question: str) -> bool:
    return bool(RE_LIST.search(question))


def _clean(answer: str) -> str:
    """Espacios finos y negritas de Markdown (Telegram muestra los ** tal cual)."""
    answer = answer.replace("\u202f", " ").replace("\u00a0", " ").replace("**", "")
    return citations.strip_markers(answer)


def candidates(hits: list[dict], limit: int | None = None) -> list[dict]:
    """Candidatos para el modelo: los de término raro siempre; el resto, salvo ruido evidente."""
    rare = [h for h in hits if h.get("termino_raro")]
    rest = [
        h for h in hits
        if not h.get("termino_raro")
        and h["distance"] is not None and h["distance"] <= settings.NOISE_DISTANCE
    ]
    return (rare + rest)[: limit or settings.ASK_CANDIDATES]


def about_entities(question: str, hits: list[dict]) -> list[dict] | None:
    """Exige que las fuentes nombren lo específico de la pregunta (nombres propios, códigos,
    cifras). Devuelve los trozos que sirven, o None si alguna entidad no sale en ninguno: en ese
    caso la memoria no sabe de eso, por mucho que otra nota se parezca en el resto."""
    ents = verify.question_entities(question)
    if not ents:
        return hits
    keys = [verify.text_keys(f"{h['content']} {citations.cite(h)}") for h in hits]
    if any(not any(e in k for k in keys) for e in ents):
        return None
    full = [h for h, k in zip(hits, keys) if all(e in k for e in ents)]
    return full or [h for h, k in zip(hits, keys) if any(e in k for e in ents)]


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
    limit = k or (settings.ASK_CANDIDATES_LIST if wants_list(question) else settings.ASK_CANDIDATES)
    if verify.question_entities(question):
        # Con entidades se mira más allá de los candidatos: la nota que nombra al cliente
        # puede quedar lejos en distancia y aun así ser la única que sirve
        found = await search(question, max(limit * 2, 10), publish=False)
        hits = candidates(found, limit)
        pool = hits + [h for h in found if h not in hits]
        hits = (about_entities(question, pool) or [])[:limit]
    else:
        hits = candidates(await search(question, limit, publish=False), limit)
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
    answer = _clean(answer)

    # Anti-invención: cada nombre propio y cifra debe estar en las fuentes (o en la pregunta)
    texts = [h["content"] for h in hits] + [citations.cite(h) for h in hits]
    texts.append(profile.get()["asistente"]["nombre"])
    if _says_no(answer) and verify.question_entities(question):
        # Dijo que no sabe, pero hay fuentes que nombran todo lo que pide: una segunda mirada
        named = ", ".join(str(i) for i, h in enumerate(hits, 1) if about_entities(question, [h]))
        if named:
            answer, llm = await get_provider().chat_ex(messages + [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": (
                    f"Las fuentes {named} mencionan lo que se pregunta. Léelas otra vez: si "
                    "responden la pregunta, responde con sus datos exactos; si hablan de otra "
                    f'cosa, responde exactamente: "{NO_INFO}"'
                )},
            ], cloud=True, private=private)
            answer = _clean(answer)
            llm = {**llm, "segunda_mirada": True}
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
        answer = _clean(answer)
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
