"""Filtro de salida: limpia SOLO el texto que genera la IA antes de enviarlo y guardarlo.

- Quita el formato markdown (**negritas**, __subrayados__, # títulos, `código`); Telegram los
  mostraría tal cual.
- Quita los gestos narrados entre *asteriscos* o (paréntesis): "*sonríe*", "(guiña el ojo)".
  Los paréntesis normales ("(DNI)", "(04:17)") se quedan; una palabra suelta entre asteriscos
  (*énfasis*) queda sin los asteriscos.
- Quita las comillas que envuelven todo el mensaje.
- Une espacios y saltos de línea repetidos.

No toca las citas [1], la línea «Fuentes:» ni las listas: esas las arma el código y nunca pasan
por aquí. Si después de limpiar no queda nada, devuelve "" y quien llama usa un respaldo.
"""

import logging
import re
import unicodedata

log = logging.getLogger("nexus.filtro")

GESTOS = (
    "sonrie", "sonriendo", "rie", "riendo", "se rie", "risas", "guina", "guinando", "guino",
    "suspira", "suspirando", "abraza", "abrazo", "besos", "beso", "aplaude", "aplausos",
    "pensando", "piensa", "revisando", "buscando", "escribiendo", "mira", "mirando", "asiente",
    "asintiendo", "se sonroja", "sonrojada", "carcajada", "jaja", "le guina",
)
_TITULO = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_NEGRITA = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_CODIGO = re.compile(r"```(?:[a-zA-Z]+\n)?(.*?)```|`([^`\n]+)`", re.DOTALL)
_ASTERISCOS = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_PARENTESIS = re.compile(r"\(([^()\n]+)\)")
_GERUNDIO = re.compile(r"^\w+(?:ando|iendo|yendo)(?:se|me|te|le|la|lo)?\b")
_COMILLAS = {'"': '"', "“": "”", "«": "»", "'": "'", "‘": "’"}


def _plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sin if unicodedata.category(c) != "Mn").lower().strip()


def es_gesto(contenido: str, con_asteriscos: bool = False) -> bool:
    t = _plano(contenido)
    if any(t == g or t.startswith(g + " ") or t.startswith(g + ",") for g in GESTOS):
        return True
    return con_asteriscos and (t.startswith("se ") or bool(_GERUNDIO.match(t)))


def _markdown(texto: str) -> str:
    texto = _CODIGO.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), texto)
    texto = _NEGRITA.sub(lambda m: m.group(1) or m.group(2), texto)
    return _TITULO.sub("", texto)


def _gestos(texto: str) -> str:
    texto = _ASTERISCOS.sub(lambda m: "" if es_gesto(m.group(1), True) else m.group(1), texto)
    return _PARENTESIS.sub(lambda m: "" if es_gesto(m.group(1)) else m.group(0), texto)


def _comillas(texto: str) -> str:
    t = texto.strip()
    if len(t) > 1 and t[0] in _COMILLAS and t[-1] == _COMILLAS[t[0]] \
            and t[0] not in t[1:-1] and _COMILLAS[t[0]] not in t[1:-1]:
        return t[1:-1]
    return texto


def _espacios(texto: str) -> str:
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" +([,.!?;:…])", r"\1", texto)  # lo que quedó antes de un signo al quitar un gesto
    texto = "\n".join(linea.strip() for linea in texto.split("\n"))
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return re.sub(r"^[,;:]\s*", "", texto.strip())


def limpiar(texto: str) -> str:
    """Respuesta de la IA sin formato de asistente. "" si no queda nada."""
    if not texto:
        return ""
    limpio = _espacios(_comillas(_gestos(_markdown(texto))))
    if limpio != texto.strip():
        log.debug("filtro: limpió la respuesta (%d -> %d caracteres)", len(texto), len(limpio))
    return limpio
