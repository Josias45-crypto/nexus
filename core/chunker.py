def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Divide el texto en trozos de ~size caracteres, respetando párrafos
    y con solapamiento para no cortar ideas por la mitad."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    chunks: list[str] = []
    current = ""
    for p in (p.strip() for p in text.split("\n\n")):
        if not p:
            continue
        # Párrafo gigante: se parte a la fuerza
        while len(p) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(p[:size])
            p = p[size - overlap:]
        if not current:
            current = p
        elif len(current) + 2 + len(p) <= size:
            current = f"{current}\n\n{p}"
        else:
            chunks.append(current)
            tail = current[len(current) - overlap:]
            current = f"{tail}\n\n{p}"
    if current:
        chunks.append(current)
    return chunks
