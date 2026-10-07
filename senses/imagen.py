"""Imágenes: OCR local con Tesseract y, si se activa, descripción con un modelo de visión
de Ollama local (NEXUS_VISION_MODEL; apagado por defecto). Nada sale de la máquina."""

import base64
import io
import logging
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from config import settings
from core.chunker import Segment
from senses import IMAGE_EXT, Unsupported, register

log = logging.getLogger("nexus.senses.imagen")

MAX_SIDE = 4000
VISION_PROMPT = (
    "Describe en español, en 2 a 4 frases, qué muestra esta imagen. Menciona objetos, "
    "personas, lugares y cualquier texto visible. No inventes lo que no se ve."
)


def _clean(text: str) -> str:
    """Quita líneas de ruido del OCR (sueltas, casi sin letras ni números)."""
    keep = []
    for line in text.splitlines():
        line = line.strip()
        alnum = sum(ch.isalnum() for ch in line)
        if not line or (alnum >= 2 and alnum >= len(line.replace(" ", "")) * 0.5):
            keep.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def ocr(image) -> str:
    """Texto de una imagen PIL con Tesseract (idiomas NEXUS_OCR_LANGS)."""
    from PIL import ImageOps

    if not settings.OCR_ENABLED:
        raise Unsupported("el OCR está apagado (NEXUS_OCR=off)")
    if shutil.which("tesseract") is None:
        raise Unsupported("OCR no disponible: falta tesseract en la imagen de Docker")
    img = ImageOps.exif_transpose(image).convert("L")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    elif max(img.size) < 1500:  # capturas pequeñas: Tesseract lee mejor el texto ampliado
        img = img.resize((img.width * 2, img.height * 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", settings.OCR_LANGS, "--psm", "3"],
        input=buf.getvalue(), capture_output=True, timeout=settings.OCR_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"tesseract: {r.stderr.decode(errors='replace').strip()[-200:]}")
    return _clean(r.stdout.decode("utf-8", errors="replace"))


def describe(image) -> str:
    """Descripción con el modelo de visión local, si está configurado. Nunca usa la nube."""
    if not settings.VISION_MODEL:
        return ""
    img = image.convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    try:
        r = httpx.post(
            f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            json={
                "model": settings.VISION_MODEL,
                "stream": False,
                "messages": [{
                    "role": "user",
                    "content": VISION_PROMPT,
                    "images": [base64.b64encode(buf.getvalue()).decode()],
                }],
            },
            timeout=httpx.Timeout(300, connect=10),
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("No se pudo describir la imagen con %s: %s", settings.VISION_MODEL, e)
        return ""


def open_image(path: Path):
    from PIL import Image, UnidentifiedImageError

    try:
        return Image.open(path)
    except UnidentifiedImageError:
        raise Unsupported(f"imagen dañada o en un formato no soportado ({path.suffix.lower()})")
    except Image.DecompressionBombError:
        raise Unsupported("imagen demasiado grande (más de 178 millones de píxeles)")


@register("imagen", tuple(IMAGE_EXT), ("image/",))
def image(path: Path, depth: int) -> list[Segment]:
    from PIL import ImageSequence

    img = open_image(path)
    frames = [img] if path.suffix.lower() == ".gif" else list(ImageSequence.Iterator(img))
    segments = []
    for i, frame in enumerate(frames, 1):
        try:
            text = ocr(frame.copy())
        except Unsupported:
            if not settings.VISION_MODEL:
                raise
            break
        if text:
            segments.append(Segment(text, {"pagina": i, "ocr": True} if len(frames) > 1 else {"ocr": True}))
    description = describe(img)
    if description:
        segments.append(Segment(f"Descripción de la imagen: {description}", {"descripcion": True}))
    return segments
