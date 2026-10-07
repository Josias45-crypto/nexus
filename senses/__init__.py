"""Sentidos: convierten un original en segmentos de texto con su origen (página, hoja,
diapositiva, minuto, archivo dentro de un .zip...).

Cada módulo registra sus extractores con @register. extract() elige por extensión y, si no
la reconoce, por tipo MIME. Todo es local: el OCR y la transcripción no salen de la máquina.
"""

import logging
import mimetypes
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config import settings
from core.chunker import Segment

log = logging.getLogger("nexus.senses")

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma", ".amr"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".3gp", ".mpeg", ".mpg", ".wmv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_DEPTH = 2  # un .zip dentro de un correo, como mucho

# Formatos conocidos que no se leen: el motivo le dice al usuario qué hacer
KNOWN_UNSUPPORTED = {
    ".doc": "Word antiguo (.doc): guárdalo como .docx o PDF",
    ".xls": "Excel antiguo (.xls): guárdalo como .xlsx o CSV",
    ".ppt": "PowerPoint antiguo (.ppt): guárdalo como .pptx o PDF",
    ".msg": "correo de Outlook (.msg): guárdalo como .eml",
    ".rar": "solo se abren comprimidos .zip",
    ".7z": "solo se abren comprimidos .zip",
    ".heic": "foto HEIC: envíala como JPG",
    ".heif": "foto HEIF: envíala como JPG",
}


class Unsupported(Exception):
    """No se puede sacar texto de este original; el mensaje explica por qué."""


@dataclass
class Extractor:
    name: str
    exts: frozenset[str]
    mimes: tuple[str, ...]  # tipos exactos o prefijos terminados en "/"
    func: Callable[[Path, int], list[Segment]]


_REGISTRY: list[Extractor] = []


def register(name: str, exts: tuple[str, ...] = (), mimes: tuple[str, ...] = ()):
    def deco(func):
        _REGISTRY.append(Extractor(name, frozenset(exts), mimes, func))
        return func

    return deco


def find(path: Path, mime: str) -> Extractor | None:
    ext = path.suffix.lower()
    for ex in _REGISTRY:
        if ext in ex.exts:
            return ex
    if ext in KNOWN_UNSUPPORTED:
        return None
    mime = (mime or "").split(";")[0].strip().lower()
    for ex in _REGISTRY:
        if any(mime == m or (m.endswith("/") and mime.startswith(m)) for m in ex.mimes):
            return ex
    return None


def formats() -> dict[str, list[str]]:
    """Extensiones que entiende cada extractor (para la documentación y /health)."""
    return {ex.name: sorted(ex.exts) for ex in _REGISTRY}


def extract(path: Path, mime: str = "", depth: int = 0) -> list[Segment]:
    """Segmentos del original. Lanza Unsupported si no hay forma de leerlo."""
    ex = find(path, mime)
    if ex is None:
        from senses import texto

        if texto.looks_like_text(path):
            ex = next(e for e in _REGISTRY if e.name == "texto")
        else:
            ext = path.suffix.lower()
            reason = KNOWN_UNSUPPORTED.get(ext) or (
                f"formato no soportado ({ext or 'sin extensión'}, {mime or 'tipo desconocido'})"
            )
            raise Unsupported(reason[0].upper() + reason[1:])
    segments = [s for s in ex.func(path, depth) if s.text.strip()]
    return _cap(segments) if depth == 0 else segments


def _cap(segments: list[Segment]) -> list[Segment]:
    total, kept = 0, []
    for s in segments:
        if total + len(s.text) > settings.MAX_TEXT_CHARS:
            log.warning(
                "Texto recortado a %d caracteres (NEXUS_MAX_TEXT_CHARS)", settings.MAX_TEXT_CHARS
            )
            break
        total += len(s.text)
        kept.append(s)
    return kept


def extract_member(name: str, data: bytes, mime: str, depth: int) -> list[Segment]:
    """Extrae un archivo de dentro de otro (.zip, adjunto de correo). Las secciones llevan
    el nombre del archivo interno en "archivo" y empiezan con un encabezado propio."""
    suffix = Path(name).suffix.lower()
    if depth > MAX_DEPTH:
        raise Unsupported("demasiados niveles de archivos dentro de archivos")
    if suffix in AUDIO_EXT | VIDEO_EXT:
        raise Unsupported("audio o video dentro de otro archivo: súbelo aparte")
    mime = mime or mimetypes.guess_type(name)[0] or ""
    tmp_dir = Path(settings.DATA_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=suffix[:16]) as tmp:
        tmp.write(data)
        tmp.flush()
        inner = extract(Path(tmp.name), mime, depth)
    if not inner:
        return []
    head = Segment(f"# {name}", {"archivo": name})
    return [head] + [Segment(s.text, {"archivo": name, **s.meta}, s.markdown) for s in inner]


# Registro: el orden importa solo si dos extractores declaran la misma extensión
from senses import texto, documentos, oficina, correo, imagen, audio, comprimidos  # noqa: E402,F401
