"""Citas armadas por el código (no por el modelo).

El modelo redacta la respuesta; el código decide qué trozos la respaldan (palabras con
contenido compartidas entre respuesta y trozo) y arma la referencia legible:
"informe.pdf, pág. 3", "nota_de_voz.ogg, min 01:23", "manual.md, § Instalación".
"""

import re
import unicodedata

STOPWORDS = set(
    "para pero como esta este esto estos estas tiene tienen sobre entre desde hasta donde cuando "
    "porque segun según cual cuales quien quienes también tambien solo sólo muy más mas menos "
    "fuente fuentes respuesta pregunta información informacion memoria tengo eres aqui aquí "
    "ellos ellas nosotros usted ustedes todo todos toda todas otro otra otros otras cada mismo "
    "misma sido será sera eran fueron hace hacer puede pueden debe deben unos unas algo".split()
)
MIN_SHARED = 2  # palabras con contenido en común para considerar que un trozo respalda


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", _norm(text)) if len(w) >= 4 and w not in STOPWORDS}


def strip_markers(answer: str) -> str:
    """Quita citas inventadas por el modelo: [1], (Fuente 2), Fuente 3:."""
    nums = r"\d+(?:\s*(?:y|,)\s*\d+)*"
    answer = re.sub(rf"\s*\[{nums}\]", "", answer)
    answer = re.sub(rf"^\s*fuentes?\s*{nums}\s*[:\-]\s*", "", answer, flags=re.I)
    answer = re.sub(rf"\s*\((?:seg[uú]n\s+)?(?:las?\s+)?fuentes?\s*{nums}\)", "", answer, flags=re.I)
    answer = re.sub(rf"\b(?:las?\s+)?fuentes?\s*{nums}", "mis notas", answer, flags=re.I)
    return re.sub(r"\s+([.,;:])", r"\1", answer).strip()


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _span(meta: dict, key: str) -> str:
    end = meta.get(f"{key}_fin")
    return f"{meta[key]}–{end}" if end else str(meta[key])


def location(meta: dict) -> str | None:
    """Dónde está el trozo: "pág. 2–3", "min 01:23", "hoja Ventas, fila 4", "diap. 5",
    "cap. 2", "§ Sección"; dentro de un .zip o un adjunto, antecedido del archivo interno."""
    if not meta:
        return None
    if meta.get("resumen"):
        return "resumen"
    where = None
    if "pagina" in meta:
        where = f"pág. {_span(meta, 'pagina')}"
    elif "inicio" in meta:
        where = f"min {_mmss(meta['inicio'])}"
    elif "hoja" in meta:
        where = f"hoja {meta['hoja']}" + (f", fila {_span(meta, 'fila')}" if "fila" in meta else "")
    elif "fila" in meta:
        where = f"fila {_span(meta, 'fila')}"
    elif "diapositiva" in meta:
        where = f"diap. {_span(meta, 'diapositiva')}"
    elif "capitulo" in meta:
        where = f"cap. {_span(meta, 'capitulo')}"
    if "seccion" in meta and meta["seccion"] != meta.get("archivo") and not where:
        where = f"§ {meta['seccion']}"
    parts = [p for p in (meta.get("archivo"), where) if p]
    return ", ".join(parts) or None


def cite(hit: dict) -> str:
    loc = location(hit.get("meta") or {})
    return f"{hit['filename']}, {loc}" if loc else hit["filename"]


def supporting(answer: str, hits: list[dict]) -> list[dict]:
    """Trozos que respaldan la respuesta, mejor primero, uno por documento.
    Si ninguno comparte palabras suficientes, el más relevante de la búsqueda."""
    words = content_words(answer)
    scored = []
    for rank, h in enumerate(hits):
        shared = len(words & content_words(h["content"]))
        if shared >= MIN_SHARED:
            scored.append((-shared, rank, h))
    chosen = [h for _, _, h in sorted(scored)] or hits[:1]
    seen, result = set(), []
    for h in chosen:
        if h["event_id"] not in seen:
            seen.add(h["event_id"])
            result.append(h)
    return result
