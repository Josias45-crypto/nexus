"""Interfaz común de canales (Telegram, WhatsApp, simulado...).

Un canal solo sabe recibir y enviar mensajes en su plataforma. Toda la lógica de
conversación vive en channels/assistant.py y habla con NEXUS por su API HTTP.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    START = "start"
    HELP = "help"
    REMEMBER = "recordar"  # guardar información en la memoria
    ASK = "preguntar"
    FILE = "archivo"
    VOICE = "voz"
    STATUS = "estado"
    TODAY = "hoy"
    PRIVATE = "privado"
    REMINDER = "recordatorio"  # "recuérdame ..."
    REMINDERS = "recordatorios"
    DONE = "hecho"
    YES = "si"
    NO = "no"


@dataclass
class Incoming:
    channel: str
    chat_id: str
    user_id: str
    text: str = ""
    # Archivo adjunto: se descarga solo si hace falta (los canales lo implementan)
    filename: str | None = None
    mime: str | None = None
    is_voice: bool = False
    size: int | None = None
    download: Callable[[], Awaitable[bytes]] | None = field(default=None, repr=False)


COMMANDS = {
    "start": Intent.START,
    "ayuda": Intent.HELP,
    "help": Intent.HELP,
    "recordar": Intent.REMEMBER,
    "pregunta": Intent.ASK,
    "preguntar": Intent.ASK,
    "estado": Intent.STATUS,
    "hoy": Intent.TODAY,
    "privado": Intent.PRIVATE,
    "recordatorios": Intent.REMINDERS,
    "hecho": Intent.DONE,
}
RE_COMMAND = re.compile(r"^/(\w+)(?:@\w+)?\s*(.*)$", re.S)
RE_REMINDER = re.compile(r"^\s*(recu[eé]rdame|recordarme|av[ií]same)\b", re.I)
YES = {"si", "sí", "s", "ok", "dale", "listo", "guardalo", "guárdalo", "correcto", "confirmo", "yes"}
NO = {"no", "n", "cancelar", "cancela", "nop"}


def detect_intent(msg: Incoming, awaiting_confirmation: bool = False) -> tuple[Intent, str]:
    """Devuelve (intención, argumento de texto)."""
    if msg.download is not None:
        return (Intent.VOICE if msg.is_voice else Intent.FILE), msg.text.strip()
    text = msg.text.strip()
    if m := RE_COMMAND.match(text):
        intent = COMMANDS.get(m.group(1).lower())
        if intent:
            return intent, m.group(2).strip()
        return Intent.HELP, ""
    if awaiting_confirmation:
        word = re.sub(r"[^\wáéíóúñ]", "", text.lower())
        if word in YES:
            return Intent.YES, ""
        if word in NO:
            return Intent.NO, ""
    if RE_REMINDER.match(text):
        return Intent.REMINDER, text
    # Un texto libre sin comando se trata como pregunta
    return Intent.ASK, text


class PermanentSendError(Exception):
    """El canal nunca podrá entregar este mensaje (p. ej. fuera de la ventana de 24 h de
    WhatsApp): el aviso se da por cerrado en vez de reintentarlo para siempre."""


class Channel(ABC):
    name: str

    @abstractmethod
    def receive(self) -> AsyncIterator[Incoming]:
        """Mensajes entrantes (de cualquier usuario: el asistente filtra a los autorizados)."""

    @abstractmethod
    async def send(self, chat_id: str, text: str) -> None:
        """Envía un texto. Debe lanzar excepción si no se pudo entregar."""

    @abstractmethod
    def owners(self) -> list[str]:
        """Chats de los dueños autorizados (destino de los avisos sin chat concreto)."""

    async def typing(self, chat_id: str) -> None:
        """Muestra "escribiendo..." mientras se prepara la respuesta (opcional por canal)."""
