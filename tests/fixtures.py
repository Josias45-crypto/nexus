"""Archivos de prueba de cada formato, generados en memoria (solo librería estándar,
salvo image_with_text/scanned_pdf/video, que usan Pillow y PyAV dentro del contenedor)."""

import io
import zipfile
from email.message import EmailMessage

CT = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def docx(heading: str, paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = (
        f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{heading}</w:t></w:r></w:p>'
        + "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    )
    if table:
        body += "<w:tbl>" + "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in table
        ) + "</w:tbl>"
    return _zip({
        "word/document.xml": f'{CT}<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>',
    })


def xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    shared: list[str] = []
    files = {}
    book_sheets, rels = [], []
    for n, (name, rows) in enumerate(sheets.items(), 1):
        xml_rows = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if isinstance(value, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        files[f"xl/worksheets/sheet{n}.xml"] = (
            f'{CT}<worksheet xmlns="{s}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
        )
        book_sheets.append(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>')
        rels.append(f'<Relationship Id="rId{n}" Type="{DOC_REL}/worksheet" Target="worksheets/sheet{n}.xml"/>')
    files["xl/workbook.xml"] = (
        f'{CT}<workbook xmlns="{s}" xmlns:r="{DOC_REL}"><sheets>{"".join(book_sheets)}</sheets></workbook>'
    )
    files["xl/_rels/workbook.xml.rels"] = f'{CT}<Relationships xmlns="{REL_NS}">{"".join(rels)}</Relationships>'
    files["xl/sharedStrings.xml"] = (
        f'{CT}<sst xmlns="{s}">' + "".join(f"<si><t>{v}</t></si>" for v in shared) + "</sst>"
    )
    return _zip(files)


def pptx(slides: list[tuple[str, str]], notes: dict[int, str] | None = None) -> bytes:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    files, ids, rels = {}, [], []
    # Orden de presentación distinto del nombre de archivo: la 2.ª diapositiva es slide1.xml
    order = list(range(len(slides)))[::-1]
    for pos, idx in enumerate(order):
        title, text = slides[pos]
        n = idx + 1
        files[f"ppt/slides/slide{n}.xml"] = (
            f'{CT}<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree>'
            f"<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
            f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>"
        )
        if notes and pos + 1 in notes:
            files[f"ppt/notesSlides/notesSlide{n}.xml"] = (
                f'{CT}<p:notes xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:txBody>'
                f"<a:p><a:r><a:t>{notes[pos + 1]}</a:t></a:r></a:p><a:p><a:r><a:t>{pos + 1}</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
            )
            files[f"ppt/slides/_rels/slide{n}.xml.rels"] = (
                f'{CT}<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" '
                f'Type="{DOC_REL}/notesSlide" Target="../notesSlides/notesSlide{n}.xml"/></Relationships>'
            )
        ids.append(f'<p:sldId id="{256 + pos}" r:id="rId{pos + 1}"/>')
        rels.append(f'<Relationship Id="rId{pos + 1}" Type="{DOC_REL}/slide" Target="slides/slide{n}.xml"/>')
    files["ppt/presentation.xml"] = (
        f'{CT}<p:presentation xmlns:p="{p}" xmlns:r="{DOC_REL}"><p:sldIdLst>{"".join(ids)}'
        "</p:sldIdLst></p:presentation>"
    )
    files["ppt/_rels/presentation.xml.rels"] = f'{CT}<Relationships xmlns="{REL_NS}">{"".join(rels)}</Relationships>'
    return _zip(files)


def epub(chapters: list[tuple[str, str]]) -> bytes:
    items = "".join(
        f'<item id="c{i}" href="Text/cap%20{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(chapters))
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": (
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>"
        ),
        "OEBPS/content.opf": (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f"<manifest>{items}</manifest><spine>{spine}</spine></package>"
        ),
    }
    for i, (title, text) in enumerate(chapters):
        files[f"OEBPS/Text/cap {i}.xhtml"] = (
            f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title><style>p{{}}</style></head>'
            f"<body><h1>{title}</h1><p>{text}</p></body></html>"
        )
    return _zip(files)


def eml(subject: str, body: str, attachments: dict[str, bytes] | None = None) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "Ana <ana@example.com>"
    msg["To"] = "dueno@example.com"
    msg["Date"] = "Tue, 06 Oct 2026 10:00:00 -0500"
    msg.set_content(body)
    for name, data in (attachments or {}).items():
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


def zip_of(files: dict[str, bytes | str]) -> bytes:
    return _zip(files)


def image_with_text(lines: list[str]) -> bytes:
    """PNG con texto negro sobre blanco (necesita Pillow)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=44)
    img = Image.new("RGB", (1400, 90 * len(lines) + 80), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((50, 40 + 90 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def scanned_pdf(lines: list[str]) -> bytes:
    """PDF de una página que es solo una imagen (como un escaneo)."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_with_text(lines)))
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150)
    return buf.getvalue()


def video_from_audio(audio: bytes, audio_ext: str = ".wav", with_audio: bool = True) -> bytes:
    """MP4 con una imagen fija y, si se pide, la pista del audio dado (necesita PyAV)."""
    import av
    import numpy as np

    src = av.open(io.BytesIO(audio), format=audio_ext.lstrip(".")) if with_audio else None
    buf = io.BytesIO()
    out = av.open(buf, "w", format="mp4")
    vstream = out.add_stream("mpeg4", rate=1)
    vstream.width, vstream.height, vstream.pix_fmt = 64, 64, "yuv420p"
    seconds = 2
    if src is not None:
        astream = out.add_stream("aac", rate=16000)
        astream.layout = "mono"
        seconds = int(src.duration / 1_000_000) + 1 if src.duration else 5
    for i in range(seconds):
        frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), 40 * (i % 6), np.uint8), format="rgb24")
        for packet in vstream.encode(frame):
            out.mux(packet)
    for packet in vstream.encode():
        out.mux(packet)
    if src is not None:
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in src.decode(audio=0):
            for rframe in resampler.resample(frame):
                for packet in astream.encode(rframe):
                    out.mux(packet)
        for packet in astream.encode():
            out.mux(packet)
        src.close()
    out.close()
    return buf.getvalue()
