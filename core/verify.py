"""Verificación por código de una respuesta: cada nombre propio y cada cifra que menciona
debe aparecer en las fuentes recuperadas (o en la pregunta). Sin modelo: solo texto."""

import re
import unicodedata

# Palabras que suelen ir con mayúscula al inicio de una frase sin ser nombres
STARTERS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del", "de", "en", "y",
    "o", "a", "con", "sin", "por", "para", "segun", "ademas", "tambien", "ambos", "ambas",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "ellos", "ellas", "el",
    "ella", "si", "no", "hay", "tiene", "tienen", "debe", "deben", "pidio", "pidieron", "quiere",
    "queria", "compro", "compraron", "son", "es", "fue", "fueron", "solo", "todos", "todas",
    "ningun", "ninguno", "ninguna", "nadie", "cliente", "clientes", "total", "en", "hoy",
    "ayer", "manana", "fuente", "fuentes", "respuesta", "nota", "notas", "estos", "aqui",
    "esto", "eso", "aqui", "asi", "luego", "despues", "antes", "sus", "su",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
}
CONNECTORS = {"de", "del", "la", "las", "los", "san", "santa"}
NUMBER_WORDS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "cien": 100, "mil": 1000,
}

RE_NUMBER = re.compile(r"\d+(?:[.,\s]\d{3})*(?:[.,]\d+)?")
RE_WORD = re.compile(r"[^\W\d_]+", re.U)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _digits(number: str) -> str:
    """'4.500' y '4 500' -> '4500'; '80,5' -> '80,5' (decimal)."""
    n = number.strip()
    if re.fullmatch(r"\d{1,3}(?:[.\s,]\d{3})+", n):
        return re.sub(r"[.\s,]", "", n)
    return n.replace(".", ",")


def names(answer: str) -> list[str]:
    """Secuencias de palabras con mayúscula (nombres propios probables)."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?:;\n])\s+|\n", answer):
        tokens = list(RE_WORD.finditer(sentence))
        i = 0
        while i < len(tokens):
            w = tokens[i].group()
            if not w[0].isupper():
                i += 1
                continue
            seq = [w]
            j = i + 1
            while j < len(tokens):
                nxt = tokens[j].group()
                if sentence[tokens[j - 1].end():tokens[j].start()].strip():
                    break  # coma u otro signo entre palabras: otro nombre
                if nxt[0].isupper():
                    seq.append(nxt)
                elif norm(nxt) in CONNECTORS and j + 1 < len(tokens) and tokens[j + 1].group()[0].isupper():
                    seq.append(nxt)
                else:
                    break
                j += 1
            if not (i == 0 and len(seq) == 1 and norm(w) in STARTERS):
                out.append(" ".join(seq))
            i = j
    return out


def unverified(answer: str, sources: list[str], question: str = "") -> list[str]:
    """Nombres y cifras de la respuesta que no aparecen en las fuentes ni en la pregunta."""
    base = norm(" ".join(sources) + " " + question)
    words = set(RE_WORD.findall(base))
    numbers = {_digits(n) for n in RE_NUMBER.findall(base)}
    numbers |= {str(v) for w, v in NUMBER_WORDS.items() if w in words}
    missing: list[str] = []
    for name in names(answer):
        parts = [p for p in name.split() if norm(p) not in CONNECTORS and len(p) >= 2]
        parts = [p for p in parts if not (len(parts) == 1 and norm(p) in STARTERS)]
        if any(norm(p) not in words for p in parts) and name not in missing:
            missing.append(name)
    for n in RE_NUMBER.findall(answer):
        n = n.strip()
        if n and _digits(n) not in numbers and n not in missing:
            missing.append(n)
    return missing


# ---------- entidades de la pregunta ----------

QUESTION_WORDS = {
    "que", "quien", "quienes", "cual", "cuales", "cuando", "cuanto", "cuanta", "cuantos",
    "cuantas", "como", "donde", "adonde", "por", "porque", "dime", "dame", "sabes", "hay",
    "recuerdas", "oye", "hola", "nexus",
}
RE_TOKEN = re.compile(r"\d+(?:[.,\s]\d{3})+(?![\d])|\w+", re.U)


def _key(token: str) -> str:
    """Forma comparable: sin acentos ni mayúsculas; '3 200' y '3.200' -> '3200'."""
    t = norm(token)
    return re.sub(r"[.,\s]", "", t) if re.fullmatch(r"\d+(?:[.,\s]\d{3})+", t) else t


def question_entities(question: str) -> list[str]:
    """Nombres propios, códigos y cifras de la pregunta (forma comparable, sin repetir).
    Se ignoran las palabras interrogativas y la mayúscula de inicio de oración."""
    out: list[str] = []
    for sentence in re.split(r"[.!?¿¡:;\n]+", question):
        for i, m in enumerate(RE_TOKEN.finditer(sentence)):
            tok = m.group()
            key = _key(tok)
            if key in QUESTION_WORDS or key in STARTERS or key in CONNECTORS:
                continue
            if any(c.isdigit() for c in tok):
                pass  # cifra o código (L3250, Quimera7f3a): siempre cuenta
            elif not tok[0].isupper() or i == 0 or len(tok) < 2:
                continue  # minúscula o primera palabra de la oración
            if key not in out:
                out.append(key)
    return out


def text_keys(text: str) -> set[str]:
    return {_key(t) for t in RE_TOKEN.findall(text or "")}
