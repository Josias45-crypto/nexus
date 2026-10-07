"""PDF: texto por página y, en páginas escaneadas (sin texto), OCR local."""

import logging
from pathlib import Path

from config import settings
from core.chunker import Segment
from senses import Unsupported, register

log = logging.getLogger("nexus.senses.pdf")

MIN_PAGE_CHARS = 25  # menos que esto en una página: probablemente es una imagen escaneada
OCR_SCALE = 200 / 72  # 200 ppp


def _ocr_pages(path: Path, pages: list[int]) -> dict[int, str]:
    import pypdfium2 as pdfium

    from senses.imagen import ocr

    out = {}
    pdf = pdfium.PdfDocument(str(path))
    try:
        for n in pages:
            bitmap = pdf[n - 1].render(scale=OCR_SCALE)
            out[n] = ocr(bitmap.to_pil())
    finally:
        pdf.close()
    return out


@register("pdf", (".pdf",), ("application/pdf",))
def pdf(path: Path, depth: int) -> list[Segment]:
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
        texts = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as e:
        raise Unsupported(f"PDF dañado ({e})")

    scanned = [i for i, t in enumerate(texts, 1) if len(t.strip()) < MIN_PAGE_CHARS]
    if scanned and settings.OCR_ENABLED:
        todo = scanned[: settings.OCR_MAX_PAGES]
        if len(scanned) > len(todo):
            log.warning(
                "%d páginas escaneadas; OCR solo de las primeras %d (NEXUS_OCR_MAX_PAGES)",
                len(scanned), len(todo),
            )
        try:
            ocr_texts = _ocr_pages(path, todo)
        except Unsupported as e:  # sin tesseract: queda lo que tenga texto
            log.warning("Sin OCR para %s: %s", path.name, e)
            ocr_texts = {}
    else:
        ocr_texts = {}

    segments = []
    for i, text in enumerate(texts, 1):
        if i in ocr_texts and len(ocr_texts[i]) > len(text.strip()):
            segments.append(Segment(ocr_texts[i], {"pagina": i, "ocr": True}))
        else:
            segments.append(Segment(text, {"pagina": i}))
    return segments
