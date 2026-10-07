"""Intérprete determinista de fechas en español para recordatorios (sin LLM).

Entiende: hoy, mañana, pasado mañana, días de la semana ("el viernes", "el próximo lunes"),
"en N minutos/horas/días/semanas", fechas "15 de octubre" y "15/10", y horas "a las 9",
"a las 9:30", "9 am", "a las 7 de la tarde", "al mediodía", "por la tarde" (16:00).

Reglas por defecto (siempre se confirman al usuario antes de guardar):
- Sin hora: 09:00. Sin día: hoy si la hora aún no pasó; si no, mañana.
- Hora de 1 a 7 sin "am"/"de la mañana": se asume de la tarde (nadie agenda a las 3 a. m.).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}
WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
NUMBERS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15,
    "veinte": 20, "treinta": 30,
}
DEFAULT_TIME = time(9, 0)

_NUM = r"(\d{1,3}|" + "|".join(NUMBERS) + r")"
_DAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH = "|".join(MONTHS)

RE_TRIGGER = re.compile(
    r"^\s*(/recordar\b|recu[eé]rdame|recordarme|recu[eé]rdale|av[ií]same)\s*(que\s+|de\s+)?",
    re.I,
)
RE_RELATIVE = re.compile(
    rf"\b(dentro de|en)\s+(media hora|{_NUM}\s+(minutos?|mins?|horas?|d[ií]as?|semanas?))\b", re.I
)
RE_PASADO = re.compile(r"\bpasado\s+ma[nñ]ana\b", re.I)
# "mañana" como día, no "de la mañana"
RE_MANANA = re.compile(r"(?<!de la )(?<!por la )\bma[nñ]ana\b", re.I)
RE_HOY = re.compile(r"\b(hoy|esta (tarde|noche))\b", re.I)
RE_WEEKDAY = re.compile(rf"\b(?:(?:el|este|esta|pr[oó]ximo|el pr[oó]ximo)\s+)?({_DAY})\b", re.I)
RE_DATE_NAME = re.compile(rf"\b(?:el\s+)?(\d{{1,2}})\s+de\s+({_MONTH})\b", re.I)
RE_DATE_NUM = re.compile(r"\b(?:el\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
RE_TIME = re.compile(
    r"\b(?:a\s+las?|a\s+la|las?)\s+(\d{1,2})(?:[:.h](\d{2}))?\s*"
    r"(a\.?\s?m\.?|p\.?\s?m\.?|de la ma[nñ]ana|de la tarde|de la noche|hrs?|horas)?(?!\w)"
    r"|\b(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)(?!\w)",
    re.I,
)
RE_NOON = re.compile(r"\b(al\s+)?mediod[ií]a\b", re.I)
RE_PART = re.compile(r"\b(?:por|en|a) la (ma[nñ]ana|tarde|noche)\b", re.I)
PART_TIMES = {"m": time(9, 0), "t": time(16, 0), "n": time(20, 0)}


@dataclass
class Parsed:
    when: datetime  # hora local (naive)
    task: str
    explicit_time: bool


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBERS[token.lower()]


def _strip(text: str, span: tuple[int, int]) -> str:
    return text[: span[0]] + " " + text[span[1]:]


def _parse_time(text: str) -> tuple[time | None, str]:
    m = RE_NOON.search(text)
    if m:
        return time(12, 0), _strip(text, m.span())
    m = RE_TIME.search(text)
    if not m:
        if p := RE_PART.search(text):
            return PART_TIMES[p.group(1)[0].lower()], _strip(text, p.span())
        return None, text
    if m.group(1):
        hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    else:
        hour, minute, suffix = int(m.group(4)), int(m.group(5) or 0), (m.group(6) or "").lower()
    if hour > 23 or minute > 59:
        return None, text
    suffix = suffix.replace(".", "").replace(" ", "")
    if suffix in ("pm", "dela tarde", "delatarde", "delanoche") or "tarde" in suffix or "noche" in suffix:
        if hour < 12:
            hour += 12
    elif suffix in ("am",) or "ma" in suffix:  # "de la mañana"
        if hour == 12:
            hour = 0
    elif 1 <= hour <= 7:
        hour += 12
    return time(hour, minute), _strip(text, m.span())


def _clean_task(text: str) -> str:
    text = RE_TRIGGER.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    # restos típicos tras quitar la fecha ("llamar a Pedro el", "de")
    text = re.sub(r"\s+(el|la|a|de|para|por|en)$", "", text, flags=re.I).strip(" ,.;:-")
    return text


def parse(text: str, now: datetime) -> Parsed | None:
    """Devuelve la fecha local entendida y la tarea, o None si no hay fecha reconocible.
    `now` es la hora local actual (naive)."""
    rest = text
    day: date | None = None
    tod: time | None = None

    m = RE_RELATIVE.search(rest)
    if m:
        if m.group(2).lower() == "media hora":
            delta = timedelta(minutes=30)
        else:
            n, unit = _num(m.group(3)), m.group(4).lower()
            if unit.startswith("min"):
                delta = timedelta(minutes=n)
            elif unit.startswith("hora"):
                delta = timedelta(hours=n)
            elif unit.startswith("semana"):
                delta = timedelta(weeks=n)
            else:
                delta = timedelta(days=n)
        rest = _strip(rest, m.span())
        target = now + delta
        if delta >= timedelta(days=1):  # "en 3 días a las 10"
            t, rest = _parse_time(rest)
            if t:
                target = datetime.combine(target.date(), t)
                return Parsed(target, _clean_task(rest), True)
        return Parsed(target.replace(second=0, microsecond=0), _clean_task(rest), True)

    t, rest = _parse_time(rest)
    tod = t

    if m := RE_PASADO.search(rest):
        day, rest = now.date() + timedelta(days=2), _strip(rest, m.span())
    elif m := RE_MANANA.search(rest):
        day, rest = now.date() + timedelta(days=1), _strip(rest, m.span())
    elif m := RE_DATE_NAME.search(rest):
        d, month = int(m.group(1)), MONTHS[m.group(2).lower()]
        day = _future_date(now.date(), month, d)
        if day is None:  # "31 de febrero"
            return None
        rest = _strip(rest, m.span())
    elif m := RE_DATE_NUM.search(rest):
        d, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year:
            y = int(year) + (2000 if len(year) == 2 else 0)
            day = _safe_date(y, month, d)
        else:
            day = _future_date(now.date(), month, d)
        if day is None:
            return None
        rest = _strip(rest, m.span())
    elif m := RE_WEEKDAY.search(rest):
        wd = WEEKDAYS[m.group(1).lower()]
        ahead = (wd - now.weekday()) % 7
        proximo = re.search(r"pr[oó]ximo", m.group(0), re.I) is not None
        if ahead == 0 and (proximo or (tod or DEFAULT_TIME) <= now.time()):
            ahead = 7
        day, rest = now.date() + timedelta(days=ahead), _strip(rest, m.span())
    elif m := RE_HOY.search(rest):
        day = now.date()
        if m.group(2) and not tod:
            tod = time(16, 0) if m.group(2).lower() == "tarde" else time(20, 0)
        rest = _strip(rest, (m.start(), m.start() + len("hoy")) if m.group(1).lower() == "hoy" else m.span())

    if day is None and tod is None:
        return None
    if day is None:
        day = now.date() if tod > now.time() else now.date() + timedelta(days=1)
    return Parsed(datetime.combine(day, tod or DEFAULT_TIME), _clean_task(rest), tod is not None)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _future_date(today: date, month: int, d: int) -> date | None:
    for y in (today.year, today.year + 1):
        candidate = _safe_date(y, month, d)
        if candidate and candidate >= today:
            return candidate
    return None


def describe(dt: datetime, now: datetime) -> str:
    """'mañana viernes 10 de octubre a las 09:00' (para confirmar al usuario)."""
    delta = (dt.date() - now.date()).days
    rel = {0: "hoy ", 1: "mañana ", 2: "pasado mañana "}.get(delta, "")
    return (
        f"{rel}{WEEKDAY_NAMES[dt.weekday()]} {dt.day} de {MONTH_NAMES[dt.month - 1]}"
        f"{'' if dt.year == now.year else f' de {dt.year}'} a las {dt:%H:%M}"
    )
