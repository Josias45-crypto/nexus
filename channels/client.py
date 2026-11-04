"""Cliente HTTP de la API de NEXUS para los canales.

La API exige sesión salvo con el token interno (cabecera X-Nexus-Token): se toma de
NEXUS_INTERNAL_TOKEN o del archivo que crea el núcleo (data/interno/token, montado en solo
lectura en el contenedor del canal). Nunca se registra.
"""

import os
from pathlib import Path

import httpx

TOKEN_ARCHIVO = Path(os.getenv("NEXUS_DATA_DIR", "/data")) / "interno" / "token"


def token_interno() -> str:
    if t := os.getenv("NEXUS_INTERNAL_TOKEN", "").strip():
        return t
    try:
        return TOKEN_ARCHIVO.read_text().strip()
    except OSError:
        return ""


class NexusError(Exception):
    """La API respondió con error; el mensaje ya viene en español."""


class NexusClient:
    def __init__(
        self, base_url: str, timeout: float = 600, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport,
                                       headers={"X-Nexus-Token": token_interno()})

    async def close(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kw):
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise NexusError("Se me cortó la conexión un momento, jefe. Inténtelo de nuevo en un ratito.") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise NexusError(detail if isinstance(detail, str) else f"Error HTTP {r.status_code}")
        return r.json()

    async def profile(self) -> dict:
        return await self._req("GET", "/profile")

    async def conversar(self, mensajes: list[str], channel: str, chat_id: str) -> dict:
        """Un turno de conversación: {"texto", "botones", "accion", "nivel", "llm"}."""
        return await self._req(
            "POST", "/conversar", json={"mensajes": mensajes, "channel": channel, "chat_id": chat_id}
        )

    async def seguir(self, channel: str, chat_id: str) -> dict:
        """La continuación de una respuesta con "seguir" (p. ej. la búsqueda ya anunciada)."""
        return await self._req("POST", "/conversar/seguir", json={"channel": channel, "chat_id": chat_id})

    async def boton(self, dato: str, channel: str, chat_id: str) -> dict:
        return await self._req(
            "POST", "/conversar/boton", json={"dato": dato, "channel": channel, "chat_id": chat_id}
        )

    async def archivo(self, archivos: list[dict], channel: str, chat_id: str, leyenda: str = "",
                      voz: bool = False, grande: bool = False) -> dict:
        """Archivos ya subidos (/inbox/file) que llegaron juntos: Isabelle los mira y responde una vez.
        archivos: [{"id", "filename", "kind", "duplicate"}]; grande: alguno no se pudo bajar."""
        return await self._req(
            "POST", "/conversar/archivo",
            json={"archivos": archivos, "channel": channel, "chat_id": chat_id, "leyenda": leyenda,
                  "voz": voz, "grande": grande},
        )

    async def ask(self, question: str) -> dict:
        return await self._req("POST", "/ask", json={"question": question})

    async def remember(self, text: str, source: str) -> dict:
        return await self._req(
            "POST", "/inbox/text", json={"text": text, "source": source}
        )

    async def upload(self, data: bytes, filename: str, mime: str, source: str) -> dict:
        return await self._req(
            "POST",
            "/inbox/file",
            params={"source": source},
            files={"file": (filename, data, mime)},
        )

    async def growth(self) -> dict:
        return await self._req("GET", "/growth", params={"days": 7})

    async def today(self) -> dict:
        return await self._req("GET", "/today")

    async def parse_reminder(self, text: str, channel: str | None = None, chat_id: str | None = None) -> dict:
        """Con canal y chat, lo entendido queda pendiente de confirmar en la base."""
        return await self._req(
            "POST", "/reminders/parse", json={"text": text, "channel": channel, "chat_id": chat_id}
        )

    async def pending_reminder(self, channel: str, chat_id: str) -> dict | None:
        return await self._req("GET", "/reminders/pending", params={"channel": channel, "chat_id": chat_id})

    async def confirm_reminder(self, channel: str, chat_id: str) -> dict:
        return await self._req("POST", "/reminders/confirm", json={"channel": channel, "chat_id": chat_id})

    async def test_reminder(self, channel: str, chat_id: str, seconds: int = 60) -> dict:
        return await self._req(
            "POST", "/reminders/test", params={"seconds": seconds},
            json={"channel": channel, "chat_id": chat_id},
        )

    async def cancel_pending(self, channel: str, chat_id: str) -> None:
        await self._req("POST", "/reminders/pending/cancel", json={"channel": channel, "chat_id": chat_id})

    async def create_reminder(self, text: str, due_at: str, channel: str, chat_id: str) -> dict:
        return await self._req(
            "POST",
            "/reminders",
            json={"text": text, "due_at": due_at, "channel": channel, "chat_id": chat_id},
        )

    async def reminders(self, chat_id: str) -> list[dict]:
        return await self._req("GET", "/reminders", params={"chat_id": chat_id})

    async def done(self, rid: int | None, chat_id: str) -> dict:
        if rid is None:
            return await self._req("POST", "/reminders/done", params={"chat_id": chat_id})
        return await self._req("POST", f"/reminders/{rid}/done")

    async def cancel(self, rid: int, chat_id: str) -> dict:
        return await self._req("POST", f"/reminders/{rid}/cancel", params={"chat_id": chat_id})

    async def outbox(self, channel: str) -> list[dict]:
        return await self._req("GET", "/outbox", params={"channel": channel})

    async def delivered(self, msg_id: int, acuse: str) -> None:
        """Entregado, con el acuse de la plataforma (message_id)."""
        await self._req("POST", f"/outbox/{msg_id}/delivered", json={"acuse": acuse})

    async def fallo(self, msg_id: int, error: str, reintentar_en: float | None = None) -> None:
        """No se pudo entregar: el núcleo lo reintenta con espera creciente."""
        await self._req("POST", f"/outbox/{msg_id}/fallo", json={"error": error, "reintentar_en": reintentar_en})
