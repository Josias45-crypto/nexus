"""Cliente HTTP de la API de NEXUS para los canales."""

import httpx


class NexusError(Exception):
    """La API respondió con error; el mensaje ya viene en español."""


class NexusClient:
    def __init__(
        self, base_url: str, timeout: float = 600, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kw):
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise NexusError(f"NEXUS no responde ({type(exc).__name__}).") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise NexusError(detail if isinstance(detail, str) else f"Error HTTP {r.status_code}")
        return r.json()

    async def profile(self) -> dict:
        return await self._req("GET", "/profile")

    async def ask(self, question: str) -> dict:
        return await self._req("POST", "/ask", json={"question": question})

    async def remember(self, text: str, source: str, private: bool = False) -> dict:
        return await self._req(
            "POST", "/inbox/text", json={"text": text, "source": source, "private": private}
        )

    async def upload(self, data: bytes, filename: str, mime: str, source: str, private: bool) -> dict:
        return await self._req(
            "POST",
            "/inbox/file",
            params={"source": source, "private": str(private).lower()},
            files={"file": (filename, data, mime)},
        )

    async def growth(self) -> dict:
        return await self._req("GET", "/growth", params={"days": 7})

    async def today(self) -> dict:
        return await self._req("GET", "/today")

    async def parse_reminder(self, text: str) -> dict:
        return await self._req("POST", "/reminders/parse", json={"text": text})

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

    async def outbox(self, channel: str) -> list[dict]:
        return await self._req("GET", "/outbox", params={"channel": channel})

    async def delivered(self, msg_id: int) -> None:
        await self._req("POST", f"/outbox/{msg_id}/delivered")
