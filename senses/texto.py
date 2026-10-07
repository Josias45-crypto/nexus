"""Texto plano, código, CSV, JSON, XML y HTML (solo librería estándar)."""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from core.chunker import Segment
from senses import register

CODE_EXT = (
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash", ".ps1",
    ".bat", ".sql", ".r", ".lua", ".pl", ".dart", ".vue", ".svelte", ".css", ".scss",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env.example", ".dockerfile", ".tf",
)
SNIFF_BYTES = 8192


def decode(data: bytes) -> str:
    """UTF-8 (con o sin BOM), UTF-16 con BOM y, si no, Windows-1252 (Excel en español)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_text(path: Path) -> str:
    return decode(path.read_bytes()).replace("\r\n", "\n")


def looks_like_text(path: Path) -> bool:
    """Para archivos sin extensión conocida: texto si no hay bytes nulos y es UTF-8 válido."""
    with open(path, "rb") as f:
        head = f.read(SNIFF_BYTES)
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start > len(head) - 4  # un carácter cortado al final de la muestra
    return True


@register("texto", (".txt", ".md", ".markdown", ".log", ".rst", ".org"), ("text/plain", "text/markdown"))
def plain(path: Path, depth: int) -> list[Segment]:
    return [Segment(read_text(path))]


@register("código", CODE_EXT, ("text/x-", "application/x-sh", "application/javascript", "text/css"))
def code(path: Path, depth: int) -> list[Segment]:
    return [Segment(read_text(path), markdown=False)]


def table_rows(rows: list[list[str]]) -> list[tuple[int, str]]:
    """Filas como "columna: valor; ..." usando la primera fila con datos como encabezado.
    Devuelve (número de fila, texto); la fila 1 es el encabezado."""
    rows = [[c.strip() for c in r] for r in rows]
    start = next((i for i, r in enumerate(rows) if any(r)), None)
    if start is None:
        return []
    header = rows[start]
    out = []
    for i, row in enumerate(rows[start + 1:], start + 2):
        cells = [
            f"{header[j] if j < len(header) and header[j] else f'col{j + 1}'}: {v}"
            for j, v in enumerate(row) if v
        ]
        if cells:
            out.append((i, "; ".join(cells)))
    if not out:  # una sola fila: sin encabezado que aplicar
        out.append((start + 1, "; ".join(v for v in header if v)))
    return out


@register("tabla", (".csv", ".tsv"), ("text/csv", "text/tab-separated-values"))
def table(path: Path, depth: int) -> list[Segment]:
    text = read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:SNIFF_BYTES], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return [Segment(t, {"fila": n}, markdown=False) for n, t in table_rows(rows)]


def _flatten(value, prefix: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _flatten(v, f"{prefix}[{i}]")
    elif value is not None and value != "":
        yield f"{prefix}: {value}" if prefix else str(value)


@register("json", (".json", ".jsonl", ".ndjson", ".geojson"), ("application/json",))
def json_file(path: Path, depth: int) -> list[Segment]:
    text = read_text(path)
    try:
        docs = [json.loads(text)]
    except json.JSONDecodeError:
        try:  # JSON Lines: un objeto por línea
            docs = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return [Segment(text, markdown=False)]
    lines = [line for doc in docs for line in _flatten(doc)]
    return [Segment("\n\n".join(lines), markdown=False)]


@register("xml", (".xml", ".svg", ".plist", ".gpx", ".kml"), ("application/xml", "text/xml"))
def xml_file(path: Path, depth: int) -> list[Segment]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return [Segment(read_text(path), markdown=False)]
    lines = []
    for el in root.iter():
        text = (el.text or "").strip()
        if text:
            lines.append(f"{el.tag.rsplit('}', 1)[-1]}: {text}")
    return [Segment("\n\n".join(lines), markdown=False)]


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object"}
    BLOCK = {
        "p", "div", "section", "article", "header", "footer", "main", "aside", "nav",
        "blockquote", "pre", "table", "tr", "ul", "ol", "dl", "dt", "dd", "figure",
        "figcaption", "hr", "form", "fieldset", "address", "body",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.heading: list[str] | None = None
        self.level = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n")
            self.heading, self.level = [], int(tag[1])
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag) and self.heading is not None:
            title = " ".join("".join(self.heading).split())
            if title:
                self.parts.append(f"{'#' * self.level} {title}\n\n")
            self.heading = None
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading is not None:
            self.heading.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    """HTML a texto con los títulos como encabezados Markdown (para el troceado)."""
    p = _HTMLText()
    p.feed(html)
    p.close()
    lines = [line.strip() for line in "".join(p.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@register("html", (".html", ".htm", ".xhtml"), ("text/html", "application/xhtml+xml"))
def html_file(path: Path, depth: int) -> list[Segment]:
    return [Segment(html_to_text(read_text(path)))]
