"""Intérprete determinista de fechas en español para recordatorios (sin LLM).

Entiende: hoy, mañana, pasado mañana, días de la semana ("el viernes", "el próximo lunes"),
"en N minutos/horas/días/semanas", fechas "15 de octubre" y "15/10", y horas "a las 9",
"9:30", "4 : 17", "4.17", "4h17", "4 y 18", "4 y cuarto", "4 y media", "4 menos cuarto",
"9 am", "a las 7 de la tarde", "mañana 9", "al mediodía", "a medianoche", "por la tarde" (16:00).

Reglas (siempre se confirman al usuario antes de guardar):
- Sin hora: 09:00.
- Hora de 1 a 12 sin am/pm, sin fecha o con "hoy": la PRÓXIMA ocurrencia entre h y h+12
  (a las 4 estando 04:15 -> 16:00; a las 4:17 -> 04:17). Con fecha: de 1 a 7 es de la tarde.
- Hora inequívoca (am/pm, "de la tarde", 16:17) que hoy ya pasó y sin fecha: se propone mañana
  y passed_today avisa para preguntarlo.
- Si la hora no se entiende o sobra un número suelto ("a las 4 : 17" mal escrito, "y 18"),
  se lanza Ambiguous: no se adivina.
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
HOUR_WORDS = {
    "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}
_HW = "|".join(HOUR_WORDS)
_CLOCK = (
    r"(?P<h>\d{{1,2}}{words})"
    r"(?:\s*(?:[:.]|h(?=\s*\d))\s*(?P<m>\d{{2}})(?!\d)"
    r"|\s+y\s+(?P<y>cuarto|media|\d{{1,2}})(?:\s+minutos?)?"
    r"|\s+menos\s+(?P<menos>cuarto|\d{{1,2}})(?:\s+minutos?)?)?"
)
_SUFFIX = (
    r"(?:\s*(?P<suf>a\.?\s?m\.?|p\.?\s?m\.?|de la ma[nñ]ana|de la madrugada|de la tarde"
    r"|de la noche|del mediod[ií]a)(?!\w))?(?:\s*(?:hrs?\.?|horas)(?!\w))?(?:\s+en punto)?"
)
# En orden: "a las 4 y 18 ...", "16:17" o "4 : 17" sin "a las", "7pm"
RE_TIMES = [
    re.compile(r"\b(?:a\s+las?|a\s+la|las?)\s+" + _CLOCK.format(words=f"|{_HW}") + _SUFFIX + r"(?!\w)", re.I),
    re.compile(r"(?<![\d/])\b(?P<h>\d{1,2})\s*[:h]\s*(?P<m>\d{2})(?!\d)" + _SUFFIX + r"(?!\w)", re.I),
    re.compile(r"\b(?P<h>\d{1,2})\s*(?P<suf>a\.?\s?m\.?|p\.?\s?m\.?)(?!\w)", re.I),
]
# "mañana 9", "hoy 16:30", "el viernes 3 pm": hora suelta tras el día
RE_DAY_HOUR = re.compile(
    rf"\b(hoy|ma[nñ]ana|{_DAY})\s+(?=\d{{1,2}}(?!\d)(?!\s*(?:de\s+(?:{_MONTH})|/)))", re.I
)
RE_NOON = re.compile(r"\b(?:al\s+|a\s+|el\s+)?mediod[ií]a\b", re.I)
RE_MIDNIGHT = re.compile(r"\b(?:a\s+la\s+|a\s+|la\s+)?medianoche\b", re.I)
# Restos que delatan una hora mal entendida: no se adivina
RE_LEFTOVER = re.compile(
    r"^(?:(?:y|menos|[:.h])\s*)?\d{1,2}\b|^(?:y|menos)\b"
    r"|\ba\s+las?\s+(?:\d|" + _HW + r")\b|^(?:a\.?\s?m|p\.?\s?m)\b",
    re.I,
)
ASK_TIME = "¿A qué hora exactamente? Ejemplo: 4:17 pm o 16:17."


class Ambiguous(ValueError):
    """La hora no se entendió con certeza: hay que preguntarla."""
RE_PART = re.compile(r"\b(?:por|en|a) la (ma[nñ]ana|tarde|noche)\b", re.I)
PART_TIMES = {"m": time(9, 0), "t": time(16, 0), "n": time(20, 0)}


@dataclass
class Parsed:
    when: datetime  # hora local (naive)
    task: str
    explicit_time: bool
    passed_today: bool = False  # hora inequívoca que hoy ya pasó: se propone mañana y se pregunta


@dataclass
class _Clock:
    hour: int  # 0-23 si exact; 1-12 si no hubo am/pm ni formato de 24 h
    minute: int
    exact: bool
    midnight: bool = False


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBERS[token.lower()]


def _strip(text: str, span: tuple[int, int]) -> str:
    return text[: span[0]] + " " + text[span[1]:]


def _hour(token: str) -> int:
    return int(token) if token.isdigit() else HOUR_WORDS[token.lower()]


def _clock(m: re.Match) -> _Clock:
    g = m.groupdict()
    hour, minute = _hour(g["h"]), int(g.get("m") or 0)
    if g.get("y"):
        minute = {"cuarto": 15, "media": 30}.get(g["y"].lower()) or int(g["y"])
    if g.get("menos"):
        less = 15 if g["menos"].lower() == "cuarto" else int(g["menos"])
        if not 0 < less < 60:
            raise Ambiguous(ASK_TIME)
        hour, minute = (hour - 1) % 24 or (12 if hour <= 12 else 0), 60 - less
    if hour > 23 or minute > 59:
        raise Ambiguous(ASK_TIME)
    suf = re.sub(r"[.\s]", "", (g.get("suf") or "").lower())
    if suf:
        if hour > 12:
            if suf in ("am", "delamañana", "delamanana", "delamadrugada"):
                raise Ambiguous(ASK_TIME)  # "16 am"
            return _Clock(hour, minute, True)
        if suf in ("pm", "delatarde", "delanoche"):
            if suf == "delanoche" and hour == 12:
                return _Clock(0, minute, True, midnight=True)
            return _Clock(hour % 12 + 12, minute, True)
        if suf == "delmediodia":
            return _Clock(12, minute, True)
        return _Clock(hour % 12, minute, True)  # am, de la mañana/madrugada
    if hour == 0 or hour > 12:
        return _Clock(hour, minute, True)  # formato de 24 h
    return _Clock(hour, minute, False)


def _parse_time(text: str) -> tuple[_Clock | None, str]:
    text = RE_DAY_HOUR.sub(lambda m: m.group(1) + " a las ", text)
    if m := RE_NOON.search(text):
        return _Clock(12, 0, True), _strip(text, m.span())
    if m := RE_MIDNIGHT.search(text):
        return _Clock(0, 0, True, midnight=True), _strip(text, m.span())
    for rx in RE_TIMES:
        if m := rx.search(text):
            return _clock(m), _strip(text, m.span())
    if p := RE_PART.search(text):
        t = PART_TIMES[p.group(1)[0].lower()]
        return _Clock(t.hour, t.minute, True), _strip(text, p.span())
    return None, text


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
            c, rest = _parse_time(rest)
            if c:
                hour = c.hour if c.exact or not 1 <= c.hour <= 7 else c.hour + 12
                target = datetime.combine(target.date(), time(hour, c.minute))
                return Parsed(target, _task(rest), True)
        return Parsed(target.replace(second=0, microsecond=0), _task(rest), True)

    clock, rest = _parse_time(rest)
    # Mientras se decide el día, una hora sin am/pm cuenta como la de la mañana
    tod = time(clock.hour % 24, clock.minute) if clock else None
    dated = False  # el usuario dio un día distinto de "hoy"

    if m := RE_PASADO.search(rest):
        day, rest, dated = now.date() + timedelta(days=2), _strip(rest, m.span()), True
    elif m := RE_MANANA.search(rest):
        day, rest, dated = now.date() + timedelta(days=1), _strip(rest, m.span()), True
    elif m := RE_DATE_NAME.search(rest):
        d, month = int(m.group(1)), MONTHS[m.group(2).lower()]
        day = _future_date(now.date(), month, d)
        if day is None:  # "31 de febrero"
            return None
        rest, dated = _strip(rest, m.span()), True
    elif m := RE_DATE_NUM.search(rest):
        d, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year:
            y = int(year) + (2000 if len(year) == 2 else 0)
            day = _safe_date(y, month, d)
        else:
            day = _future_date(now.date(), month, d)
        if day is None:
            return None
        rest, dated = _strip(rest, m.span()), True
    elif m := RE_WEEKDAY.search(rest):
        wd = WEEKDAYS[m.group(1).lower()]
        ahead = (wd - now.weekday()) % 7
        proximo = re.search(r"pr[oó]ximo", m.group(0), re.I) is not None
        if ahead == 0 and (proximo or (tod or DEFAULT_TIME) <= now.time()):
            ahead = 7
        day, rest, dated = now.date() + timedelta(days=ahead), _strip(rest, m.span()), True
    elif m := RE_HOY.search(rest):
        day = now.date()
        if m.group(2) and not tod:
            tod = time(16, 0) if m.group(2).lower() == "tarde" else time(20, 0)
        rest = _strip(rest, (m.start(), m.start() + len("hoy")) if m.group(1).lower() == "hoy" else m.span())

    task = _task(rest)
    if day is None and clock is None:
        if re.search(r"\d|\ba\s+las?\b", task):  # una hora que no se entendió
            raise Ambiguous(ASK_TIME)
        return None
    if clock is None:
        return Parsed(datetime.combine(day, tod or DEFAULT_TIME), task, tod is not None)

    base = day or now.date()
    if clock.midnight:  # "a medianoche": al terminar ese día
        return Parsed(datetime.combine(base + timedelta(days=1), time(0, clock.minute)), task, True)
    if dated:  # con fecha: de 1 a 7 sin am/pm es de la tarde (como siempre)
        hour = clock.hour if clock.exact or not 1 <= clock.hour <= 7 else clock.hour + 12
        return Parsed(datetime.combine(day, time(hour, clock.minute)), task, True)
    if not clock.exact:
        # Próxima ocurrencia entre h y h+12 (12 = mediodía o medianoche)
        options = [
            datetime.combine(base, time(clock.hour % 12, clock.minute)),
            datetime.combine(base, time(clock.hour % 12 + 12, clock.minute)),
        ]
        if clock.hour == 12:
            options = [options[1], options[0] + timedelta(days=1)]
        for dt in sorted(options):
            if dt > now:
                return Parsed(dt, task, True)
        if day is not None:  # "hoy a las 3" estando 23:00: no hay a qué hora de hoy
            raise Ambiguous(ASK_TIME)
        return Parsed(min(options) + timedelta(days=1), task, True)
    dt = datetime.combine(base, time(clock.hour, clock.minute))
    if dt <= now:  # hora inequívoca que hoy ya pasó: se propone mañana, preguntando
        return Parsed(dt + timedelta(days=1), task, True, passed_today=True)
    return Parsed(dt, task, True)


def _task(rest: str) -> str:
    task = _clean_task(rest)
    if RE_LEFTOVER.search(task):  # "17 llamar a Piter", "y 18 ...": la hora no se entendió
        raise Ambiguous(ASK_TIME)
    return task


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


def ampm(dt: datetime | time) -> str:
    """'4:17 a. m.', '12:00 p. m.' (mediodía), '12:00 a. m.' (medianoche)."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"


def describe(dt: datetime, now: datetime) -> str:
    """'hoy 4:17 a. m.', 'mañana martes 13, 9:00 a. m.', 'viernes 16 de octubre, 3:00 p. m.'"""
    delta = (dt.date() - now.date()).days
    if delta == 0:
        return f"hoy {ampm(dt)}"
    rel = {1: "mañana ", 2: "pasado mañana "}.get(delta, "")
    month = "" if rel else f" de {MONTH_NAMES[dt.month - 1]}"
    year = "" if dt.year == now.year else f" de {dt.year}"
    return f"{rel}{WEEKDAY_NAMES[dt.weekday()]} {dt.day}{month}{year}, {ampm(dt)}"
