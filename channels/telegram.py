"""Canal de Telegram por long polling (no abre puertos).

Variables: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_IDS (ids numéricos separados por comas),
NEXUS_API_URL (http://core:8000 dentro de Docker).
Ejecutar: python -m channels.telegram  (servicio "telegram", profile "channels").

Seguridad: las URLs de la API de Telegram contienen el token. Nunca se registran URLs ni
mensajes de excepción de httpx; solo el tipo de error y el código HTTP.
"""

import asyncio
import logging
import os
import re
import sys

import httpx

from channels.assistant import Assistant
from channels.base import Buttons, Channel, Incoming
from channels.client import NexusClient

log = logging.getLogger("nexus.telegram")

_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+")


class _RedactToken(logging.Filter):
    """Tapa el token en cualquier registro de httpx, sea cual sea el nivel configurado."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN_IN_URL.sub("/bot<oculto>", str(record.msg))
        if record.args:
            record.args = tuple(
                _TOKEN_IN_URL.sub("/bot<oculto>", str(a)) if isinstance(a, (str, httpx.URL)) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactToken())

API = "https://api.telegram.org"
POLL_TIMEOUT = 50  # segundos que Telegram retiene la petición si no hay mensajes
MAX_DOWNLOAD = 20 * 1024 * 1024  # límite de la API de bots para descargar archivos
MAX_TEXT = 4000  # Telegram corta en 4096


class TelegramError(Exception):
    """Error sin datos sensibles (ni token ni URL)."""


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, owner_ids: list[str], transport: httpx.AsyncBaseTransport | None = None):
        self._token = token
        self._owners = owner_ids
        self._http = httpx.AsyncClient(timeout=POLL_TIMEOUT + 15, transport=transport)
        self._offset = 0

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, **params) -> dict | list:
        try:
            r = await self._http.post(f"{API}/bot{self._token}/{method}", json=params)
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: sin conexión ({type(exc).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"{method}: respuesta inválida (HTTP {r.status_code})") from None
        if not data.get("ok"):
            # 'description' es un texto de Telegram (no contiene el token)
            raise TelegramError(f"{method}: HTTP {r.status_code} {data.get('description', '')}".strip())
        return data["result"]

    # ---------- Channel ----------

    def owners(self) -> list[str]:
        return self._owners

    async def send(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None:
        partes = [text[i : i + MAX_TEXT] for i in range(0, max(len(text), 1), MAX_TEXT)]
        for n, parte in enumerate(partes, 1):
            extra = {}
            if buttons and n == len(partes):  # los botones van en el último trozo
                extra["reply_markup"] = {"inline_keyboard": [
                    [{"text": b["texto"], "callback_data": b["dato"][:64]} for b in fila] for fila in buttons
                ]}
            await self._call("sendMessage", chat_id=chat_id, text=parte, **extra)

    async def button_done(self, msg: Incoming) -> None:
        """Confirma el clic (quita el relojito) y borra los botones: no se pueden pulsar dos veces."""
        if msg.callback_id:
            await self._call("answerCallbackQuery", callback_query_id=msg.callback_id)
        if msg.message_id:
            await self._call("editMessageReplyMarkup", chat_id=msg.chat_id, message_id=int(msg.message_id),
                             reply_markup={"inline_keyboard": []})

    async def typing(self, chat_id: str) -> None:
        await self._call("sendChatAction", chat_id=chat_id, action="typing")

    async def receive(self):
        backoff = 1
        while True:
            try:
                updates = await self._call(
                    "getUpdates", offset=self._offset, timeout=POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query"],
                )
                backoff = 1
            except TelegramError as exc:
                if "401" in str(exc):
                    raise SystemExit("TELEGRAM_BOT_TOKEN inválido (Telegram respondió 401).")
                if "409" in str(exc):
                    log.error("Otro proceso usa este bot (getUpdates o webhook activo): %s", exc)
                else:
                    log.warning("Telegram no disponible: %s; reintento en %d s", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for upd in updates:
                self._offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    msg = self.button_incoming(upd["callback_query"])
                else:
                    msg = self.to_incoming(upd.get("message") or {})
                if msg:
                    yield msg

    # ---------- conversión ----------

    def button_incoming(self, q: dict) -> Incoming | None:
        m = q.get("message") or {}
        if "from" not in q or not m.get("chat") or not q.get("data"):
            return None
        return Incoming(
            channel=self.name, chat_id=str(m["chat"]["id"]), user_id=str(q["from"]["id"]),
            button=q["data"], message_id=str(m.get("message_id") or ""), callback_id=q.get("id"),
        )

    def to_incoming(self, m: dict) -> Incoming | None:
        if not m or "from" not in m:
            return None
        base = dict(channel=self.name, chat_id=str(m["chat"]["id"]), user_id=str(m["from"]["id"]))
        text = m.get("text") or m.get("caption") or ""
        attachment = None
        if "voice" in m:
            attachment = (m["voice"], "nota_de_voz.ogg", m["voice"].get("mime_type", "audio/ogg"), True)
        elif "audio" in m:
            a = m["audio"]
            attachment = (a, a.get("file_name") or "audio.mp3", a.get("mime_type", "audio/mpeg"), False)
        elif "document" in m:
            d = m["document"]
            attachment = (d, d.get("file_name") or "archivo", d.get("mime_type", "application/octet-stream"), False)
        elif "photo" in m and m["photo"]:
            p = m["photo"][-1]  # la de mayor resolución
            attachment = (p, f"foto_{m.get('message_id', '')}.jpg", "image/jpeg", False)
        elif "video" in m:
            v = m["video"]
            attachment = (v, v.get("file_name") or "video.mp4", v.get("mime_type", "video/mp4"), False)
        elif "video_note" in m:  # video redondo grabado en el chat
            attachment = (m["video_note"], f"video_nota_{m.get('message_id', '')}.mp4", "video/mp4", False)
        if attachment:
            info, filename, mime, voice = attachment
            return Incoming(
                **base, text=text, filename=filename, mime=mime, is_voice=voice,
                size=info.get("file_size"), download=self._downloader(info),
            )
        if text:
            return Incoming(**base, text=text)
        return None

    def _downloader(self, info: dict):
        async def download() -> bytes:
            if (info.get("file_size") or 0) > MAX_DOWNLOAD:
                raise TelegramError("archivo mayor a 20 MB")
            f = await self._call("getFile", file_id=info["file_id"])
            try:
                r = await self._http.get(f"{API}/file/bot{self._token}/{f['file_path']}")
            except httpx.HTTPError as exc:
                raise TelegramError(f"descarga: sin conexión ({type(exc).__name__})") from None
            if r.status_code != 200:
                raise TelegramError(f"descarga: HTTP {r.status_code}")
            return r.content

        return download


class TelegramAssistant(Assistant):
    async def _archivo(self, msg):
        if (msg.size or 0) > MAX_DOWNLOAD:
            return {"texto": "⚠️ Telegram solo me deja descargar archivos de hasta 20 MB."}
        try:
            return await super()._archivo(msg)
        except TelegramError as exc:
            return {"texto": f"⚠️ No pude descargar el archivo de Telegram ({exc})."}


def _ids(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


async def _wait_nexus(nexus: NexusClient) -> None:
    while True:
        try:
            await nexus.profile()
            return
        except Exception:
            log.info("Esperando al núcleo en %s ...", nexus.base_url)
            await asyncio.sleep(5)


async def amain() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en .env (ver docs/CANALES.md).")
    allowed = _ids(os.getenv("TELEGRAM_ALLOWED_IDS", ""))
    if not allowed:
        log.warning(
            "TELEGRAM_ALLOWED_IDS está vacío: el bot no atenderá a nadie. Escríbele y busca "
            "tu id en este registro ('usuario no autorizado (id ...)')."
        )
    nexus = NexusClient(os.getenv("NEXUS_API_URL", "http://core:8000"))
    channel = TelegramChannel(token, allowed)
    await _wait_nexus(nexus)
    me = await channel._call("getMe")
    log.info("Bot @%s conectado; %d usuario(s) autorizado(s)", me.get("username"), len(allowed))
    bot = TelegramAssistant(channel, nexus, set(allowed))
    try:
        await bot.run(outbox_every=float(os.getenv("NEXUS_CHANNEL_OUTBOX_SECONDS", "15")))
    finally:
        await channel.close()
        await nexus.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("NEXUS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx registra la URL completa en INFO; además del filtro, se deja en WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
