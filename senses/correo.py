"""Correos .eml: encabezados, cuerpo y adjuntos que se puedan leer."""

import email
import email.policy
import logging
from pathlib import Path

from core.chunker import Segment
from senses import Unsupported, extract_member, register
from senses.texto import html_to_text

log = logging.getLogger("nexus.senses.correo")

HEADERS = (("Asunto", "subject"), ("De", "from"), ("Para", "to"), ("Cc", "cc"), ("Fecha", "date"))


@register("correo", (".eml",), ("message/rfc822",))
def eml(path: Path, depth: int) -> list[Segment]:
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    head = [f"{label}: {msg[key]}" for label, key in HEADERS if msg[key]]
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        try:
            body = body_part.get_content()
        except (LookupError, ValueError):  # juego de caracteres desconocido
            body = (body_part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if body_part.get_content_type() == "text/html":
            body = html_to_text(body)
    if not head and not body.strip():
        raise Unsupported("no parece un correo .eml")
    subject = msg["subject"] or "Correo"
    segments = [Segment(f"# {' '.join(str(subject).split())}"), Segment("\n".join(head) + "\n\n" + body.strip())]
    for part in msg.iter_attachments():
        name = part.get_filename() or "adjunto"
        data = part.get_payload(decode=True)
        if not data:
            continue
        try:
            segments += extract_member(name, data, part.get_content_type(), depth + 1)
        except Unsupported as e:
            log.info("Adjunto %s omitido: %s", name, e)
    return segments
