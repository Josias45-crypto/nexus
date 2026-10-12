"""Canal simulado: misma lógica que Telegram, sin red externa. Sirve para pruebas y desarrollo.

Autoprueba (la usa tests/e2e.py contra la instancia aislada):
    python -m channels.simulado --url http://localhost:8000

Pasos sueltos, cada uno en un proceso nuevo (e2e reinicia el contenedor entre pedir y confirmar):
    python -m channels.simulado --step pedir|confirmar|entregar --tag X
"""

import argparse
import asyncio
import logging
import sys
import time

from channels.assistant import Assistant
from channels.base import Channel, Incoming
from channels.client import NexusClient

OWNER = "1001"
STRANGER = "6666"


class SimChannel(Channel):
    name = "simulado"

    def __init__(self, owner_ids: list[str]):
        self._owners = owner_ids
        self.sent: list[tuple[str, str]] = []
        self.queue: asyncio.Queue[Incoming] = asyncio.Queue()

    async def receive(self):
        while True:
            yield await self.queue.get()

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    def owners(self) -> list[str]:
        return self._owners

    def text(self, text: str, user: str = OWNER) -> Incoming:
        return Incoming(self.name, chat_id=user, user_id=user, text=text)

    def file(self, data: bytes, filename: str, mime: str, voice: bool = False) -> Incoming:
        async def download() -> bytes:
            return data

        return Incoming(
            self.name, chat_id=OWNER, user_id=OWNER, filename=filename, mime=mime,
            is_voice=voice, size=len(data), download=download,
        )


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
    bot = Assistant(ch, nexus, {OWNER})
    check = Check()

    async def say(text: str, user: str = OWNER) -> str:
        replies = await bot.handle(ch.text(text, user))
        return "\n".join(replies)

    r = await say("/start")
    check("Hola, soy" in r and "/recordar" in r, "ayuda con nombre del perfil", r)

    r = await say("¿Qué secreto hay?", user=STRANGER)
    check(r == "" and all(c != STRANGER for c, _ in ch.sent), "ignora a usuarios no autorizados", r)

    r = await say(f"/recordar El proveedor ficticio Tralvex{tag} entrega los martes.")
    check("Lo recordaré" in r, "/recordar guarda una nota", r)

    r = await say("/privado")
    check("privado" in r, "/privado arma el siguiente envío", r)
    r = await say(f"/recordar La clave ficticia de Ulmira{tag} es 4417.")
    check("(privado)" in r, "el siguiente /recordar sale privado", r)
    r = await say(f"/recordar Nota pública Quenor{tag}.")
    check("(privado)" not in r, "/privado se consume en un solo envío", r)

    r = await bot.handle(ch.file(f"archivo sintético Pelvatra{tag}".encode(), f"doc{tag}.txt", "text/plain"))
    check(bool(r) and "Recibí" in r[0], "archivo va a la bandeja", str(r))

    r = await say("recuérdame comprar algo sin fecha")
    check("No entendí la fecha" in r or "Entendí" in r, "recordatorio sin fecha pide aclarar", r)

    r = await say(f"recuérdame en 1 minuto llamar a Tralvex{tag}")
    check("Entendí:" in r and "¿Lo guardo?" in r, "recordatorio pide confirmación", r)
    r = await say("no")
    check("no lo guardo" in r, "responder no cancela", r)
    pend = await nexus.reminders(OWNER)
    check(not any(tag in p["text"] for p in pend), "cancelado no se guarda", str(pend))

    r = await say(f"recuérdame en 1 minuto llamar a Tralvex{tag}")
    r = await say("sí, ¿y a qué hora?")
    check("No entendí tu respuesta" in r, "un sí con pregunta vuelve a preguntar", r)
    r = await say("Sí, guárdalo")
    check("Guardado (n.º" in r and "hora de Perú" in r, "responder sí guarda el recordatorio", r)
    r = await say("sí")
    check("Ya estaba guardado" in r, "un sí repetido no lo duplica", r)
    pend = await nexus.reminders(OWNER)
    check(any(tag in p["text"] for p in pend), "aparece en /recordatorios (API)", str(pend))
    r = await say("/recordatorios")
    check(tag in r and "hora de Perú" in r and "[pendiente]" in r, "/recordatorios lo lista", r)

    first = await _wait_outbox(bot, ch, f"Tralvex{tag}", timeout=150)
    check(first is not None, "el aviso llega por el canal", str(ch.sent[-3:]))
    second = await _wait_outbox(bot, ch, "aviso 2 de", timeout=90)
    check(second is not None, "insiste si no hay respuesta", str(ch.sent[-3:]))
    r = await say("/hecho")
    check("Hecho" in r, "/hecho cierra el último aviso", r)
    pend = await nexus.reminders(OWNER)
    check(not any(tag in p["text"] for p in pend), "ya no está pendiente", str(pend))

    r = await say("/estado")
    check("días de vida" in r, "/estado", r)

    await asyncio.sleep(25)  # deja que el worker procese las notas
    r = await say("/hoy")
    check(f"Quenor{tag}" in r, "/hoy muestra lo aprendido", r)
    check(f"Ulmira{tag}" not in r and "(privado)" in r, "/hoy no muestra contenido privado", r)

    r = await say(f"¿Qué día entrega el proveedor Tralvex{tag}?")
    check("martes" in r.lower(), "texto libre = pregunta a la memoria", r)

    await nexus.close()
    print(f"\nCanal simulado: {len(check.failures)} falla(s)")
    return 1 if check.failures else 0


async def step(url: str, tag: str, name: str) -> int:
    """Un paso del recordatorio en un bot recién arrancado (nada en memoria)."""
    ch = SimChannel([OWNER])
    nexus = NexusClient(url)
    bot = Assistant(ch, nexus, {OWNER})
    check = Check()
    if name == "pedir":
        r = "\n".join(await bot.handle(ch.text(f"recuérdame en 1 minuto revisar Brontal{tag}")))
        check("¿Lo guardo?" in r, "pide confirmación", r)
    elif name == "confirmar":
        r = "\n".join(await bot.handle(ch.text("Sí, guárdalo")))
        check("Guardado (n.º" in r and "hora de Perú" in r, "confirma tras el reinicio", r)
        pend = await nexus.reminders(OWNER)
        check(any(f"Brontal{tag}" in p["text"] for p in pend), "la fila existe", str(pend))
    elif name == "entregar":
        got = await _wait_outbox(bot, ch, f"Brontal{tag}", timeout=150)
        check(got is not None, "el aviso sale por /outbox", str(ch.sent[-3:]))
        r = "\n".join(await bot.handle(ch.text("/hecho")))
        check("Hecho" in r, "/hecho lo cierra", r)
    await nexus.close()
    return 1 if check.failures else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--tag", default=str(int(time.time())))
    p.add_argument("--step", choices=["pedir", "confirmar", "entregar"])
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.step:
        return asyncio.run(step(args.url, args.tag, args.step))
    return asyncio.run(selftest(args.url, args.tag))


if __name__ == "__main__":
    sys.exit(main())
