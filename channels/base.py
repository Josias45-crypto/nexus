"""Interfaz común de canales (Telegram, WhatsApp, simulado...).

Un canal solo sabe recibir y enviar mensajes en su plataforma. La conversación vive en el núcleo
(core/conversa.py, POST /conversar): sin comandos, en lenguaje natural. channels/assistant.py
junta los mensajes seguidos, muestra "escribiendo..." y envía la respuesta.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field


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
    # Botón pulsado: su dato ("rec:cancelar:12"), el mensaje que lo traía y el id del clic
    button: str | None = None
    message_id: str | None = None
    callback_id: str | None = None


# Botones dentro del chat: filas de {"texto": ..., "dato": ...}
Buttons = list[list[dict]]


class PermanentSendError(Exception):
    """El canal nunca podrá entregar este mensaje (p. ej. fuera de la ventana de 24 h de
    WhatsApp): el aviso se da por cerrado en vez de reintentarlo para siempre."""


class Channel(ABC):
    name: str

    @abstractmethod
    def receive(self) -> AsyncIterator[Incoming]:
        """Mensajes entrantes (de cualquier usuario: el asistente filtra a los autorizados)."""

    @abstractmethod
    async def send(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None:
        """Envía un texto (con botones si el canal los admite). Lanza excepción si no se pudo."""

    @abstractmethod
    def owners(self) -> list[str]:
        """Chats de los dueños autorizados (destino de los avisos sin chat concreto)."""

    async def typing(self, chat_id: str) -> None:
        """Muestra "escribiendo..." mientras se prepara la respuesta (opcional por canal)."""

    async def button_done(self, msg: Incoming) -> None:
        """Tras atender un botón: confirma el clic y quita los botones (opcional por canal)."""
