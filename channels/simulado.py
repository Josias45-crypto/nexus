"""Canal simulado: misma lógica que Telegram, sin red externa. Sirve para pruebas y desarrollo.

Autoprueba (la usa tests/e2e.py contra la instancia aislada):
    python -m channels.simulado --url http://localhost:8000

Conversación natural, sin comandos (Isabelle responde por POST /conversar).

Pasos sueltos, cada uno en un proceso nuevo (e2e reinicia el contenedor entre pedir y confirmar):
    python -m channels.simulado --step pedir|confirmar|entregar|caos|boton90|saludo_ajuste|saludo|internet_olvido|ver --tag X
"""

import argparse
import asyncio
import logging
import sys
import time

from channels.assistant import Assistant
from channels.base import Buttons, Channel, Incoming
from channels.client import NexusClient

OWNER = "1001"
STRANGER = "6666"


class SimChannel(Channel):
    name = "simulado"

    def __init__(self, owner_ids: list[str]):
        self._owners = owner_ids
        self.sent: list[tuple[str, str]] = []
        self.buttons: Buttons | None = None  # los del último mensaje enviado
        self.botones_de: list[Buttons | None] = []  # los de cada mensaje de `sent`, en orden
        self.queue: asyncio.Queue[Incoming] = asyncio.Queue()

    async def receive(self):
        while True:
            yield await self.queue.get()

    async def send(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None:
        self.sent.append((chat_id, text))
        self.buttons = buttons
        self.botones_de.append(buttons)

    def buttons_for(self, contains: str) -> Buttons | None:
        """Los botones del último mensaje enviado que contiene `contains`."""
        for (_, text), b in zip(reversed(self.sent), reversed(self.botones_de)):
            if contains in text:
                return b
        return None

    def button(self, label: str, user: str = OWNER, of: str | None = None) -> Incoming:
        """Pulsa el botón `label` del último mensaje (o del último que contiene `of`)."""
        botones = self.buttons_for(of) if of else self.buttons
        dato = next(b["dato"] for fila in botones or [] for b in fila if b["texto"] == label)
        return Incoming(self.name, chat_id=user, user_id=user, button=dato)

    def owners(self) -> list[str]:
        return self._owners

    def text(self, text: str, user: str = OWNER) -> Incoming:
        return Incoming(self.name, chat_id=user, user_id=user, text=text)

    def file(self, data: bytes, filename: str, mime: str, voice: bool = False, text: str = "",
             grupo: str | None = None) -> Incoming:
        async def download() -> bytes:
            return data

        return Incoming(
            self.name, chat_id=OWNER, user_id=OWNER, text=text, filename=filename, mime=mime,
            is_voice=voice, size=len(data), download=download, grupo=grupo,
        )


def boleta_png(tag: str) -> bytes:
    """Foto sintética de una boleta con texto, cifra y fecha (Pillow, ya está en la imagen)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    lineas = ["BOLETA DE VENTA", f"B001-{tag[-6:]}", "Cliente: Bodega Marlen", "TOTAL: S/ 450.00", "Vence: 28/11/2026"]
    font = ImageFont.load_default(size=40)
    img = Image.new("RGB", (900, 80 * len(lineas) + 60), "white")
    draw = ImageDraw.Draw(img)
    for i, linea in enumerate(lineas):
        draw.text((40, 30 + 80 * i), linea, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Check:
    def __init__(self):
        self.failures: list[str] = []

    def __call__(self, ok: bool, label: str, detail: str = "") -> None:
        print(f"{'ok ' if ok else 'FALLA'} {label}" + (f" -> {detail[:160]!r}" if not ok else ""))
        if not ok:
            self.failures.append(label)


async def _wait_outbox(bot: Assistant, ch: SimChannel, contains: str, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        before = len(ch.sent)
        await bot.deliver_outbox()
        for _, text in ch.sent[before:]:
            if contains in text:
                return text
        await asyncio.sleep(2)
    return None


async def selftest(url: str, tag: str) -> int:
    ch = SimChannel([OWNER])
    nexus = NexusClient(url)
    bot = Assistant(ch, nexus, {OWNER}, pausa=False)
    check = Check()

    async def say(text: str, user: str = OWNER) -> str:
        return "\n".join(await bot.handle(ch.text(text, user)))

    async def press(label: str) -> str:
        return "\n".join(await bot.handle(ch.button(label)))

    r = await say("hola")
    check(bool(r) and "/" not in r, "saluda sin comandos", r)

    r = await say("¿Qué secreto hay?", user=STRANGER)
    check(r == "" and all(c != STRANGER for c, _ in ch.sent), "ignora a usuarios no autorizados", r)

    r = await say(f"recuerda esto: el proveedor ficticio Tralvex{tag} entrega los martes")
    check(any(w in r.lower() for w in ("guardado", "anotado")), "«recuerda esto» guarda tal cual", r)
    await say(f"Nota de prueba Quenor{tag}, la caja chica tiene 340 soles")
    r = await say("recuerda esto")
    check(any(w in r.lower() for w in ("guardado", "anotado")), "«recuerda esto» guarda lo anterior", r)

    r = "\n".join(await bot.handle(ch.file(f"archivo sintético Pelvatra{tag}".encode(), f"doc{tag}.txt", "text/plain")))
    check("Recibí" in r, "archivo: acusa recibo con su nombre", r)

    r = await say("recuérdame comprar algo sin fecha")
    check("No entendí la fecha" in r, "recordatorio sin fecha pide aclarar", r)

    r = await say(f"recuérdame en 1 minuto llamar a Tralvex{tag}")
    check(f"llamar a Tralvex{tag}" in r and [b["texto"] for b in ch.buttons[0]] == ["Cambiar", "Cancelar"],
          "hora clara: guarda y muestra Cambiar y Cancelar", r)
    r = await press("Cancelar")
    check(f"Tralvex{tag}" in r and not ch.buttons, "botón Cancelar", r)
    pend = await nexus.reminders(OWNER)
    check(not any(tag in p["text"] for p in pend), "cancelado no queda pendiente", str(pend))

    r = await say(f"recuérdame en 1 minuto llamar a Tralvex{tag}")
    r = await press("Cambiar")
    check(r.startswith("¿") and r.endswith("?"), "botón Cambiar pregunta cuándo", r)
    r = await say("en 2 minutos")
    check(f"Tralvex{tag}" in r and ch.buttons, "cambia la hora", r)
    r = await say("¿qué recordatorios tengo?")
    check(tag in r and "n.º" in r, "lista los recordatorios en lenguaje natural", r)

    first = await _wait_outbox(bot, ch, f"Tralvex{tag}", timeout=200)
    botones = ch.buttons_for(f"Tralvex{tag}")
    check(first is not None and "/" not in first and botones
          and [b["texto"] for b in botones[0]] == ["Ya lo hice", "Recuérdame"],
          "el aviso llega con «Ya lo hice» y «Recuérdame», sin comandos", str(ch.sent[-3:]))
    second = await _wait_outbox(bot, ch, f"Tralvex{tag}", timeout=90)
    check(second is not None and second != first, "insiste si no hay respuesta", str(ch.sent[-3:]))
    r = await say("ya lo hice")
    check(f"Tralvex{tag}" in r, "«ya lo hice» cierra el último aviso", r)
    pend = await nexus.reminders(OWNER)
    check(not any(tag in p["text"] for p in pend), "ya no está pendiente", str(pend))

    r = await say("cancela el recordatorio 999999")
    check("No encuentro" in r, "cancelar uno inexistente", r)
    r = await say("¿qué puedes hacer?")
    check("sin comandos" in r, "la ayuda sale en lenguaje natural", r)

    await asyncio.sleep(25)  # deja que el worker procese las notas
    r = await say("¿qué aprendí hoy?")
    check(f"Quenor{tag}" in r, "pregunta por fecha", r)
    r = await say(f"¿Qué día entrega el proveedor Tralvex{tag}?")
    check("martes" in r.lower() and "De mi memoria:" in r, "pregunta a la memoria con fuentes legibles", r)

    r = await say("ok ok")
    check(bool(r) and "?" not in r, "«ok ok» cierra sin otra pregunta", r)

    await nexus.close()
    print(f"\nCanal simulado: {len(check.failures)} falla(s)")
    return 1 if check.failures else 0


async def step(url: str, tag: str, name: str) -> int:
    """Un paso del recordatorio en un bot recién arrancado (nada en memoria)."""
    ch = SimChannel([OWNER])
    nexus = NexusClient(url)
    bot = Assistant(ch, nexus, {OWNER}, pausa=False)
    check = Check()
    if name == "pedir":  # guarda sin preguntar y pide confirmar el borrado
        r = "\n".join(await bot.handle(ch.text(f"recuérdame en 1 minuto revisar Brontal{tag}")))
        check(f"Brontal{tag}" in r and ch.buttons, "guarda con hora clara y botones", r)
        rid = next(p["id"] for p in await nexus.reminders(OWNER) if f"Brontal{tag}" in p["text"])
        r = "\n".join(await bot.handle(ch.text(f"cancela el recordatorio {rid}")))
        check("¿Cancelo" in r, "borrar pide confirmación", r)
    elif name == "confirmar":  # tras el reinicio, el «no» responde a lo que esperaba
        r = "\n".join(await bot.handle(ch.text("no")))
        check("Lo dejo como está" in r, "la espera sobrevive al reinicio", r)
        pend = await nexus.reminders(OWNER)
        check(any(f"Brontal{tag}" in p["text"] for p in pend), "la fila sigue", str(pend))
    elif name == "entregar":
        got = await _wait_outbox(bot, ch, f"Brontal{tag}", timeout=150)
        check(got is not None, "el aviso sale por /outbox", str(ch.sent[-3:]))
        r = "\n".join(await bot.handle(ch.text("ya lo hice")))
        check(f"Brontal{tag}" in r, "«ya lo hice» lo cierra", r)
    elif name == "boton90":  # el camino del bot real: crear a 90 s, aviso en /outbox, botón «Ya lo hice»
        r = "\n".join(await bot.handle(ch.text(f"recuérdame en 90 segundos revisar la caja Corvel{tag}")))
        check(f"Corvel{tag}" in r and ch.buttons, "creado con hora clara", r)
        got = await _wait_outbox(bot, ch, f"Corvel{tag}", timeout=150)
        botones = ch.buttons_for(f"Corvel{tag}")
        check(got is not None and botones and botones[0][0]["texto"] == "Ya lo hice",
              "el aviso llega con «Ya lo hice»", str(ch.sent[-2:]))
        r = "\n".join(await bot.handle(ch.button("Ya lo hice", of=f"Corvel{tag}")))
        check(f"Corvel{tag}" in r, "«Ya lo hice» lo cierra", r)
        rows = [p for p in await nexus.reminders(OWNER) if f"Corvel{tag}" in p["text"]]
        check(rows == [], "ya no está pendiente ni sin confirmar", str(rows))
    elif name == "saludo_ajuste":  # ajuste del saludo en lenguaje natural (también fija a dónde escribirle)
        r = "\n".join(await bot.handle(ch.text("desde hoy el saludo de la noche a las 10")))
        check("10:00 p. m." in r, "«desde hoy el saludo de la noche a las 10» lo cambia", r)
    elif name == "saludo":  # el programador deja el saludo en /outbox y el bot lo entrega por su cuenta
        deadline, got = time.monotonic() + 120, None
        while time.monotonic() < deadline and not got:
            before = len(ch.sent)
            await bot.deliver_outbox()
            got = next((t for c, t in ch.sent[before:] if c == OWNER), None)
            await asyncio.sleep(2)
        check(bool(got) and "/" not in got, "el saludo llega solo, sin comandos", str(ch.sent[-2:]))
        r = "\n".join(await bot.handle(ch.text("ok ok")))
        check(bool(r) and "?" not in r, "tras «ok ok» no hace otra pregunta", r)
        estado = await nexus._req("GET", "/iniciativa")
        enviados = [s for s in estado["saludos"] if s["estado"] == "enviado"]
        check(len(enviados) == 1 and enviados[0]["respondido"] == 1, "un solo saludo, marcado como respondido",
              str(estado["saludos"])[:300])
    elif name == "internet_olvido":  # 1 búsqueda real: aviso aparte, fuentes, «sí» la guarda; luego «olvida»
        enviados = await bot.handle(ch.text("busca en internet cuál es la capital de Australia"))
        check(len(enviados) == 2 and "internet" in enviados[0], "aviso corto y aparte antes de buscar", str(enviados)[:200])
        final = enviados[-1] if enviados else ""
        check("Fuentes de internet:" in final and "¿Quiere que lo guarde" in final, "responde con fuentes y ofrece guardar", final)
        r = "\n".join(await bot.handle(ch.text("sí")))
        check("nota de internet" in r, "«sí» la guarda como nota de internet", r)
        await bot.handle(ch.text(f"recuerda esto: el proveedor ficticio Brontal{tag} cobra 70 soles por envío"))
        visto = False
        for _ in range(45):  # la nota se procesa en el worker (trozos e índice)
            hits = await nexus._req("GET", "/search", params={"q": f"Brontal{tag}", "k": 5})
            if any(f"Brontal{tag}" in h.get("content", "") for h in hits):
                visto = True
                break
            await asyncio.sleep(2)
        check(visto, "la nota quedó en la memoria", "")
        r = "\n".join(await bot.handle(ch.text(f"olvida lo de Brontal{tag}")))
        check("¿Confirma que olvide" in r and "7 días" in r, "pide confirmar por texto, sin botones", r)
        check(not ch.buttons, "sin botones", str(ch.buttons))
        r = "\n".join(await bot.handle(ch.text("sí")))
        check("olvidé" in r and "7 días" in r, "«sí» lo borra y lo dice", r)
        hits = await nexus._req("GET", "/search", params={"q": f"Brontal{tag}", "k": 5})
        check(not any(f"Brontal{tag}" in h.get("content", "") for h in hits), "ya no está en la memoria", str(hits)[:200])
    elif name == "ver":  # Fase 3: UNA llamada real de visión; el repetido y la pregunta usan lo guardado
        async def enviar(inc: Incoming) -> str:
            return "\n".join(await bot.handle(inc))

        foto = boleta_png(tag)
        r = await enviar(ch.file(foto, f"boleta{tag}.png", "image/png"))
        check("450" in r and "¿es correcto?" in r and "/" not in r.replace("S/", "").replace("28/11/2026", ""),
              "comenta la foto y pide confirmar la cifra leída", r)
        check(r.count("?") == 1, "como máximo una pregunta", r)
        r = await enviar(ch.text("sí"))
        check("confirmado" in r, "«sí» confirma lo leído", r)
        r = await enviar(ch.file(foto, f"otra{tag}.png", "image/png"))
        check("ya me la había mostrado" in r or "ya la tengo guardada" in r.lower(), "el repetido se reconoce sin IA", r)
        r = await enviar(ch.text("¿cuánto era el total de la boleta de la Bodega Marlen?"))
        check("450" in r, "la pregunta usa lo guardado", r)
        r = await enviar(ch.file(b"\x00\x00\x00\x18ftypmp42" + tag.encode(), f"video{tag}.mp4", "video/mp4"))
        check("no los veo" in r or "solo los guardo" in r, "el video solo se guarda", r)
        uso = await nexus._req("GET", "/usage")
        vision = sum(m["peticiones"] for d in uso["dias"] for m in d["por_modelo"] if m["proveedor"] == "openrouter-vision")
        check(vision == 1, "una sola llamada de visión", str(vision))
    elif name == "caos":  # OpenRouter inalcanzable: una nota se guarda y una pregunta avisa en una línea
        r = "\n".join(await bot.handle(ch.text(f"recuerda esto: nota durante el caos Vendral{tag}")))
        check(any(w in r.lower() for w in ("guardado", "anotado")), "guarda la nota sin OpenRouter", r)
        r = "\n".join(await bot.handle(ch.text(f"¿Hacia dónde fluye el río Ondarel{tag}?")))
        check("\n" not in r.strip() and "conexión" in r and "guardado" in r and "OpenRouter" not in r,
              "responde una sola línea clara", r)
    await nexus.close()
    return 1 if check.failures else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--tag", default=str(int(time.time())))
    p.add_argument("--step", choices=["pedir", "confirmar", "entregar", "caos", "boton90", "saludo_ajuste", "saludo", "internet_olvido", "ver"])
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.step:
        return asyncio.run(step(args.url, args.tag, args.step))
    return asyncio.run(selftest(args.url, args.tag))


if __name__ == "__main__":
    sys.exit(main())
