"""Troceado que respeta la estructura.

Entrada: segmentos (texto + metadatos de origen: página, marca de tiempo, hoja...).
Salida: trozos de ~size caracteres que
- no parten palabras ni, si se puede, frases;
- empiezan en un encabezado Markdown cuando lo hay y recuerdan su sección;
- llevan un solapamiento que empieza en frase o palabra completa;
- conservan de qué página/tiempo vienen (y hasta cuál, si cruzan segmentos).
"""

import re
from dataclasses import dataclass, field

RE_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
RE_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Si un trozo cruza segmentos, guarda también dónde termina (pagina_fin, fila_fin...)
RANGE_KEYS = ("pagina", "inicio", "fila", "diapositiva", "capitulo")


@dataclass
class Segment:
    text: str
    meta: dict = field(default_factory=dict)
    markdown: bool = True  # False en código, CSV, JSON...: "# algo" no es un encabezado


@dataclass
class _Block:
    text: str
    meta: dict
    section: str | None
    heading: bool = False


def _split_long(text: str, size: int) -> list[str]:
    """Parte un párrafo largo por frases y, si una frase no cabe, por palabras."""
    pieces: list[str] = []
    current = ""
    for sentence in RE_SENTENCE_END.split(text):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _blocks(segments: list[Segment], size: int) -> list[_Block]:
    blocks: list[_Block] = []
    section: str | None = None
    for seg in segments:
        text = seg.text.replace("\r\n", "\n")
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            lines = para.split("\n")
            # Un encabezado puede venir pegado al párrafo que sigue
            m = RE_HEADING.match(lines[0]) if seg.markdown else None
            if m:
                section = m.group(2).strip()
                blocks.append(_Block(lines[0].strip(), seg.meta, section, heading=True))
                para = "\n".join(lines[1:]).strip()
                if not para:
                    continue
            for piece in _split_long(para, size) if len(para) > size else [para]:
                blocks.append(_Block(piece, seg.meta, section))
    return blocks


def _tail(text: str, overlap: int) -> str:
    """Final del trozo para solapar, empezando en frase o al menos en palabra completa."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    m = RE_SENTENCE_END.search(tail)
    if m and m.end() < len(tail) - 20:
        return tail[m.end():].strip()
    space = tail.find(" ")
    return tail[space + 1:].strip() if space != -1 else ""


def _meta(blocks: list[_Block]) -> dict:
    # El origen lo da el primer bloque con contenido (un encabezado suele traer menos datos)
    first = next((b for b in blocks if not b.heading), blocks[0])
    last = blocks[-1]
    meta = dict(first.meta)
    for key in RANGE_KEYS:
        if key in first.meta and last.meta.get(key) not in (None, first.meta.get(key)):
            meta[f"{key}_fin"] = last.meta[key]
    if blocks[0].section:
        meta["seccion"] = blocks[0].section
    return meta


def chunk_segments(segments: list[Segment], size: int = 1000, overlap: int = 150) -> list[tuple[str, dict]]:
    blocks = _blocks(segments, size)
    chunks: list[tuple[str, dict]] = []
    current: list[_Block] = []
    length = 0
    carry = ""

    def flush():
        nonlocal current, length, carry
        if not current:
            return
        body = "\n\n".join(b.text for b in current)
        text = f"{carry}\n\n{body}" if carry else body
        chunks.append((text, _meta(current)))
        carry = _tail(body, overlap)
        current, length = [], 0

    for b in blocks:
        if b.heading and current:
            flush()
            carry = ""  # una sección nueva no arrastra texto de la anterior
        extra = len(b.text) + (2 if current else 0)
        if current and length + extra + len(carry) > size:
            flush()
        current.append(b)
        length += extra
    flush()
    return chunks


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Compatibilidad: trocea un texto plano y devuelve solo los textos."""
    return [t for t, _ in chunk_segments([Segment(text)], size, overlap)]
