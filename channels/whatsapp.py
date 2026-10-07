"""Canal de WhatsApp por la API oficial (WhatsApp Business Cloud API de Meta).

APAGADO por defecto (NEXUS_WHATSAPP=off). Exige cuenta de Meta Business, un número de
WhatsApp Business y una dirección pública con HTTPS que apunte a este webhook
(ver docs/CANALES.md). No usa librerías no oficiales.

Variables: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET (firma),
WHATSAPP_VERIFY_TOKEN (verificación del webhook), WHATSAPP_ALLOWED_NUMBERS,
WHATSAPP_GRAPH_VERSION, NEXUS_API_URL.
Ejecutar: python -m channels.whatsapp  (servicio "whatsapp", profile "whatsapp").
"""

import asyncio
import hashlib
import hmac
import logging
import os
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from channels.assistant import Assistant
from channels.base import Channel, Incoming, PermanentSendError
from channels.client import NexusClient

log = logging.getLogger("nexus.whatsapp")

GRAPH = "https://graph.facebook.com"
OUTSIDE_WINDOW = 131047  # Meta: más de 24 h desde el último mensaje del usuario


def valid_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Verifica X-Hub-Signature-256 (HMAC-SHA256 del cuerpo con el App Secret)."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class WhatsAppChannel(Channel):
    name = "whatsapp"

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        owners: list[str],
        version: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._headers = {"Authorization": f"Bearer {token}"}
        self._phone_id = phone_number_id
        self._owners = owners
        self._base = f"{GRAPH}/{version}"
        self._http = httpx.AsyncClient(timeout=60, transport=transport)
        self.queue: asyncio.Queue[Incoming] = asyncio.Queue()

    def owners(self) -> list[str]:
        return self._owners

    async def receive(self):
        while True:
            yield await self.queue.get()

    async def send(self, chat_id: str, text: str) -> None:
        try:
            r = await self._http.post(
                f"{self._base}/{self._phone_id}/messages",
                headers=self._headers,
                json={"messaging_product": "whatsapp", "to": chat_id, "type": "text",
                      "text": {"body": text[:4000]}},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"WhatsApp sin conexión ({type(exc).__name__})") from None
        if r.status_code >= 400:
            code = (r.json().get("error") or {}).get("code") if r.content else None
            if code == OUTSIDE_WINDOW:
                raise PermanentSendError(
                    "fuera de la ventana de 24 h de WhatsApp (el dueño debe escribir primero)"
                )
            raise RuntimeError(f"WhatsApp respondió HTTP {r.status_code} (código {code})")

    def _downloader(self, media_id: str):
        async def download() -> bytes:
            meta = await self._http.get(f"{self._base}/{media_id}", headers=self._headers)
            if meta.status_code != 200:
                raise RuntimeError(f"WhatsApp: medio no disponible (HTTP {meta.status_code})")
            r = await self._http.get(meta.json()["url"], headers=self._headers)
            if r.status_code != 200:
                raise RuntimeError(f"WhatsApp: descarga fallida (HTTP {r.status_code})")
            return r.content

        return download

    def to_incoming(self, m: dict) -> Incoming | None:
        sender = m.get("from")
        if not sender:
            return None
        base = dict(channel=self.name, chat_id=sender, user_id=sender)
        kind = m.get("type")
        if kind == "text":
            return Incoming(**base, text=m["text"].get("body", ""))
        if kind in ("audio", "document", "image", "video"):
            media = m[kind]
            names = {"audio": "audio.ogg", "image": "imagen.jpg", "video": "video.mp4"}
            return Incoming(
                **base,
                text=media.get("caption", ""),
                filename=media.get("filename") or names.get(kind, "archivo"),
                mime=media.get("mime_type", "application/octet-stream"),
                is_voice=kind == "audio" and bool(media.get("voice")),
                download=self._downloader(media["id"]),
            )
        return None

    def ingest(self, payload: dict) -> int:
        """Encola los mensajes de un webhook ya verificado."""
        n = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for m in change.get("value", {}).get("messages", []):
                    msg = self.to_incoming(m)
                    if msg:
                        self.queue.put_nowait(msg)
                        n += 1
        return n


def build_app(channel: WhatsAppChannel, verify_token: str, app_secret: str) -> FastAPI:
    app = FastAPI(title="NEXUS WhatsApp", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/webhook")
    def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and verify_token and hmac.compare_digest(
            q.get("hub.verify_token", ""), verify_token
        ):
            return PlainTextResponse(q.get("hub.challenge", ""))
        return Response(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request):
        body = await request.body()
        if not valid_signature(app_secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("Webhook con firma inválida rechazado")
            return Response(status_code=403)
        try:
            channel.ingest(await request.json())
        except ValueError:
            return Response(status_code=400)
        return {"ok": True}  # responder rápido: Meta reintenta si tarda

    return app


async def amain() -> None:
    import uvicorn

    if os.getenv("NEXUS_WHATSAPP", "off").lower() != "on":
        raise SystemExit("WhatsApp está apagado (NEXUS_WHATSAPP=off). Ver docs/CANALES.md.")
    required = ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"]
    missing = [k for k in required if not os.getenv(k, "").strip()]
    if missing:
        raise SystemExit(f"Faltan variables en .env: {', '.join(missing)}")
    owners = [x.strip() for x in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if x.strip()]
    channel = WhatsAppChannel(
        os.environ["WHATSAPP_TOKEN"],
        os.environ["WHATSAPP_PHONE_NUMBER_ID"],
        owners,
        os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0"),
    )
    nexus = NexusClient(os.getenv("NEXUS_API_URL", "http://core:8000"))
    bot = Assistant(channel, nexus, set(owners))
    app = build_app(channel, os.environ["WHATSAPP_VERIFY_TOKEN"], os.environ["WHATSAPP_APP_SECRET"])
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="warning"))
    await asyncio.gather(server.serve(), bot.run())


def main() -> None:
    logging.basicConfig(
        level=os.getenv("NEXUS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
