"""PDF: texto por página. Las páginas escaneadas (sin texto) no se leen aquí: core/medios.py las
manda al modelo de visión (Fase 3), con paginas_jpeg()."""

import io
import logging
from pathlib import Path

from core.chunker import Segment
from senses import Unsupported, register

log = logging.getLogger("nexus.senses.pdf")

MIN_PAGE_CHARS = 25  # menos que esto en una página: probablemente es una imagen escaneada
RENDER_SCALE = 150 / 72  # 150 ppp: el modelo de visión lee bien y la imagen pesa poco
LADO_MAX = 1600


def textos(path: Path) -> list[str]:
    """Texto de cada página (vacío en las escaneadas). Lanza Unsupported si no se puede abrir."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # Muchos PDF vienen cifrados solo con contraseña de propietario (vacía al abrir)
            try:
                opened = reader.decrypt("")
            except Exception:
                opened = 0
            if not opened:
                raise Unsupported("PDF protegido con contraseña")
        return [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as e:
        raise Unsupported(f"PDF dañado ({e})")


def escaneadas(texts: list[str]) -> list[int]:
    """Números de página (desde 1) que no traen texto: probablemente son imágenes escaneadas."""
    return [i for i, t in enumerate(texts, 1) if len(t.strip()) < MIN_PAGE_CHARS]


def paginas_jpeg(path: Path, numeros: list[int]) -> dict[int, bytes]:
    """Las páginas pedidas como JPEG (para el modelo de visión)."""
    import pypdfium2 as pdfium

    out = {}
    pdf = pdfium.PdfDocument(str(path))
    try:
        for n in numeros:
            img = pdf[n - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
            img.thumbnail((LADO_MAX, LADO_MAX))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            out[n] = buf.getvalue()
    finally:
        pdf.close()
    return out


@register("pdf", (".pdf",), ("application/pdf",))
def pdf(path: Path, depth: int) -> list[Segment]:
    return [Segment(t, {"pagina": i}) for i, t in enumerate(textos(path), 1) if len(t.strip()) >= MIN_PAGE_CHARS]
