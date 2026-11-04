"""Nombres de persona en lo que escribe Isabelle: ninguno puede ser inventado.

Un nombre de persona es una palabra con mayúscula en posición de nombre:
- tras un título: «señor Joaquín», «don Pedro», «ingeniera Paredes»;
- en vocativo: «Hola, Joaquín», «¡Qué bueno, Joaquín!», «Joaquín, ¿cómo está?»;
- o un nombre de pila común en cualquier lugar.
Vale solo si sale de las fuentes (ficha, notas, la conversación y su mensaje) o es el jefe
(nombre_jefe), sus tratamientos o Isabelle. Lo usan la charla (core/conversa.py) y los saludos
(core/iniciativa.py); los comentarios de fotos ya exigen que todo nombre salga del análisis.
"""

import re
import unicodedata

from core import persona

TITULOS = r"(?:se[nñ]or|se[nñ]ora|se[nñ]orita|don|do[nñ]a|ingenier[oa]|gerente|jefe|licenciad[oa]|doctor|doctora|profe|profesor|profesora|sr\.?|sra\.?|srta\.?|ing\.?|lic\.?|dr\.?|dra\.?)"
NOMBRE = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}"
RE_TITULO = re.compile(rf"\b(?i:{TITULOS})\s+({NOMBRE})")  # el título en cualquier caja; el nombre, con mayúscula
# Vocativo: «Hola, X» / «Buenas noches, X!» o «…, X!» al cerrar una frase. Al empezar una frase
# («Joaquín, buenos días») solo cuentan los nombres de pila conocidos (PILA): «Listo,» no es un nombre
RE_VOCATIVO = re.compile(
    rf"\b(?i:hola|buenas|buenos d[ií]as|buenas tardes|buenas noches|hey|oye|mira|gracias|ay)[\s,]+({NOMBRE})(?=\s*[,!?.…]|$)"
    rf"|,\s*({NOMBRE})\s*(?=[!?.…]|$)", re.M)
# Nombres de pila comunes en el Perú y en general (si aparecen sueltos, también cuentan)
PILA = {
    "joaquin", "jose", "juan", "luis", "carlos", "jorge", "miguel", "pedro", "pablo", "manuel", "javier", "diego", "daniel",
    "david", "fernando", "francisco", "antonio", "alejandro", "andres", "ricardo", "roberto", "raul", "sergio", "victor",
    "hugo", "oscar", "eduardo", "enrique", "alberto", "mario", "martin", "julio", "cesar", "jaime", "ruben", "rafael",
    "alfredo", "gustavo", "hector", "ivan", "jesus", "marco", "marcos", "mateo", "nicolas", "santiago", "sebastian",
    "tomas", "rodrigo", "gabriel", "emilio", "felipe", "ernesto", "esteban", "gonzalo", "ignacio", "joel", "kevin",
    "maria", "ana", "rosa", "carmen", "lucia", "laura", "sofia", "valeria", "camila", "andrea", "patricia", "claudia",
    "gabriela", "daniela", "fernanda", "paola", "monica", "veronica", "silvia", "teresa", "elena", "isabel", "julia",
    "marta", "martha", "beatriz", "diana", "natalia", "carolina", "alejandra", "adriana", "milagros", "rocio", "susana",
    "jessica", "karina", "liliana", "lorena", "pilar", "raquel", "sandra", "vanessa", "ximena", "yolanda", "gloria",
}
SIEMPRE = {"isabelle", "jefe", "senor", "gerente", "ingeniero", "peru", "lima", "dios", "hola", "gracias", "claro",
           "listo", "perfecto", "genial", "bueno", "buenas", "oye", "mira", "vaya", "uy", "ay", "ok", "si", "no"}


def plano(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sin if unicodedata.category(c) != "Mn").lower()


def _permitidos(fuentes: list[str]) -> set[str]:
    p = persona.get()
    base = {persona.nombre_jefe(), p["identidad"]["nombre"], *p["trato"]["tratamientos"]}
    palabras = set(re.findall(r"\w+", plano(" ".join(base) + " " + " ".join(fuentes))))
    return palabras | SIEMPRE


def inventados(texto: str, fuentes: list[str]) -> list[str]:
    """Nombres de persona del texto que no salen de las fuentes ni son el jefe o Isabelle."""
    ok = _permitidos(fuentes)
    candidatos = [m.group(1) for m in RE_TITULO.finditer(texto)]
    candidatos += [g for m in RE_VOCATIVO.finditer(texto) for g in m.groups() if g]
    candidatos += [w for w in re.findall(rf"\b{NOMBRE}\b", texto) if plano(w) in PILA]
    out: list[str] = []
    for w in candidatos:
        if plano(w) not in ok and w not in out:
            out.append(w)
    return out


def instruccion(nombres: list[str]) -> str:
    """Lo que se le pide a la IA al regenerar."""
    p = persona.get()["trato"]["tratamientos"]
    return (f"No uses {', '.join(nombres)}: ese nombre no está en lo que sabes. El jefe se llama {persona.nombre_jefe()}; "
            f"llámalo solo {', '.join(p)}, y no nombres a ninguna otra persona que no esté en lo que sabes. "
            "Responde de nuevo su último mensaje.")
