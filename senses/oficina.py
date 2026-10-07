"""Word (.docx), Excel (.xlsx), PowerPoint (.pptx) y libros .epub.

Todos son un .zip con XML adentro: se leen con la librería estándar, sin dependencias.
"""

import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from config import settings
from core.chunker import Segment
from senses import Unsupported, register
from senses.texto import decode, html_to_text, table_rows

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def _open(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        # Office guarda los documentos con contraseña en otro formato (no zip)
        raise Unsupported("documento protegido con contraseña o dañado")


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > settings.ARCHIVE_MAX_MB * 1024 * 1024:
        raise Unsupported(f"documento demasiado grande por dentro ({name})")
    return ET.fromstring(z.read(info))


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relaciones de una parte (id -> ruta dentro del zip)."""
    folder, name = posixpath.split(part)
    root = _xml(z, posixpath.join(folder, "_rels", name + ".rels"))
    out = {}
    for rel in root.iter(f"{PR}Relationship") if root is not None else ():
        target = rel.get("Target", "")
        out[rel.get("Id")] = (
            target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        )
    return out


# ---------- Word ----------


def _run_text(el: ET.Element) -> str:
    parts = []
    for node in el.iter():
        if node.tag == f"{W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _heading_level(p: ET.Element) -> int:
    style = p.find(f"{W}pPr/{W}pStyle")
    val = (style.get(f"{W}val") or "") if style is not None else ""
    m = re.match(r"(?i)(heading|t[ií]?tulo|ttulo)\s*(\d)", val)
    if m:
        return min(int(m.group(2)), 6)
    if re.fullmatch(r"(?i)title|t[ií]?tulo|ttulo", val):
        return 1
    level = p.find(f"{W}pPr/{W}outlineLvl")
    return int(level.get(f"{W}val")) + 1 if level is not None else 0


def _docx_blocks(parent: ET.Element, out: list[str]) -> None:
    for el in parent:
        if el.tag == f"{W}p":
            text = _run_text(el)
            if not text:
                continue
            level = _heading_level(el)
            if level:
                out.append(f"{'#' * level} {' '.join(text.split())}")
            elif el.find(f"{W}pPr/{W}numPr") is not None:
                out.append(f"- {text}")
            else:
                out.append(text)
        elif el.tag == f"{W}tbl":
            for tr in el.iter(f"{W}tr"):
                cells = [" ".join(_run_text(p) for p in tc.iter(f"{W}p")).strip() for tc in tr.findall(f"{W}tc")]
                if any(cells):
                    out.append(" | ".join(cells))
        elif el.tag == f"{W}sdt":  # controles de contenido (índices, portadas)
            content = el.find(f"{W}sdtContent")
            if content is not None:
                _docx_blocks(content, out)


@register("word", (".docx", ".docm", ".dotx"), (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.",
))
def docx(path: Path, depth: int) -> list[Segment]:
    with _open(path) as z:
        root = _xml(z, "word/document.xml")
        if root is None:
            raise Unsupported("no es un documento de Word válido")
        body = root.find(f"{W}body")
        blocks: list[str] = []
        if body is not None:
            _docx_blocks(body, blocks)
    return [Segment("\n\n".join(blocks))]


# ---------- Excel ----------


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _sheet_rows(root: ET.Element, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in root.iter(f"{S}row"):
        cells: dict[int, str] = {}
        for i, c in enumerate(row.findall(f"{S}c")):
            kind = c.get("t")
            v = c.find(f"{S}v")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{S}t"))
            elif v is None or v.text is None:
                continue
            elif kind == "s":
                value = shared[int(v.text)] if int(v.text) < len(shared) else ""
            elif kind == "b":
                value = "VERDADERO" if v.text == "1" else "FALSO"
            else:
                value = v.text
            col = _col(c.get("r", "")) if c.get("r") else i
            cells[col] = value
        n = int(row.get("r", len(rows) + 1))
        while len(rows) < n - 1:  # filas vacías intermedias: mantienen la numeración
            rows.append([])
        rows.append([cells.get(j, "") for j in range(max(cells) + 1)] if cells else [])
    return rows


@register("excel", (".xlsx", ".xlsm"), ("application/vnd.openxmlformats-officedocument.spreadsheetml.",))
def xlsx(path: Path, depth: int) -> list[Segment]:
    with _open(path) as z:
        sst = _xml(z, "xl/sharedStrings.xml")
        shared = (
            ["".join(t.text or "" for t in si.iter(f"{S}t")) for si in sst.findall(f"{S}si")]
            if sst is not None else []
        )
        book = _xml(z, "xl/workbook.xml")
        if book is None:
            raise Unsupported("no es un libro de Excel válido")
        rels = _rels(z, "xl/workbook.xml")
        segments = []
        for sheet in book.iter(f"{S}sheet"):
            name = sheet.get("name") or "Hoja"
            target = rels.get(sheet.get(f"{R}id"))
            root = _xml(z, target) if target else None
            if root is None:
                continue
            rows = table_rows(_sheet_rows(root, shared))
            if rows:
                segments.append(Segment(f"# Hoja {name}", {"hoja": name}))
                segments += [Segment(t, {"hoja": name, "fila": n}, markdown=False) for n, t in rows]
    return segments


# ---------- PowerPoint ----------


def _paragraphs(root: ET.Element) -> list[str]:
    out = []
    for p in root.iter(f"{A}p"):
        text = "".join(t.text or "" for t in p.iter(f"{A}t")).strip()
        if text:
            out.append(text)
    return out


@register("powerpoint", (".pptx", ".pptm", ".ppsx"), (
    "application/vnd.openxmlformats-officedocument.presentationml.",
))
def pptx(path: Path, depth: int) -> list[Segment]:
    with _open(path) as z:
        pres = _xml(z, "ppt/presentation.xml")
        if pres is None:
            raise Unsupported("no es una presentación de PowerPoint válida")
        rels = _rels(z, "ppt/presentation.xml")
        slides = [rels[s.get(f"{R}id")] for s in pres.iter(f"{P}sldId") if s.get(f"{R}id") in rels]
        segments = []
        for n, part in enumerate(slides, 1):
            root = _xml(z, part)
            if root is None:
                continue
            lines = _paragraphs(root)
            notes_part = next(
                (t for t in _rels(z, part).values() if "notesSlide" in t), None
            )
            notes_root = _xml(z, notes_part) if notes_part else None
            notes = [l for l in _paragraphs(notes_root) if not l.isdigit()] if notes_root is not None else []
            if notes:
                lines.append("Notas: " + " ".join(notes))
            if lines:
                segments.append(Segment(f"## Diapositiva {n}", {"diapositiva": n}))
                segments.append(Segment("\n\n".join(lines), {"diapositiva": n}))
    return segments


# ---------- EPUB ----------


@register("epub", (".epub",), ("application/epub+zip",))
def epub(path: Path, depth: int) -> list[Segment]:
    with _open(path) as z:
        container = _xml(z, "META-INF/container.xml")
        rootfile = container.find(f".//{CONTAINER}rootfile") if container is not None else None
        if rootfile is None:
            raise Unsupported("no es un libro EPUB válido")
        opf_path = rootfile.get("full-path", "")
        opf = _xml(z, opf_path)
        if opf is None:
            raise Unsupported("no es un libro EPUB válido")
        base = posixpath.dirname(opf_path)
        manifest = {
            item.get("id"): posixpath.normpath(posixpath.join(base, urllib.parse.unquote(item.get("href", ""))))
            for item in opf.iter(f"{OPF}item")
        }
        names = set(z.namelist())
        segments = []
        chapter = 0
        for ref in opf.iter(f"{OPF}itemref"):
            href = manifest.get(ref.get("idref"))
            if not href or href not in names:
                continue
            text = html_to_text(decode(z.read(href)))
            if text:
                chapter += 1
                segments.append(Segment(text, {"capitulo": chapter}))
    return segments
