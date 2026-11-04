"""Cliente único del chat: OpenRouter (API compatible con OpenAI).

- Reintenta 429, 5xx, timeout y fallos de red con espera creciente (1 s, 2 s, 4 s).
- Solo usa proveedores con retención cero y que no guardan ni entrenan con los datos
  (provider.zdr y provider.data_collection=deny).
- Registra tokens y costo de cada respuesta (core/usage.py) y respeta el presupuesto diario:
  al alcanzarlo avisa y espera a mañana; no hay respaldo.
- El modelo potente (análisis a fondo) tiene un modelo de respaldo propio: entra solo si el
  potente está caído, limitado (429) o no existe; nunca por falta de saldo, clave o tope diario.
- Búsqueda en internet (web=True): plugin «web» con el buscador Exa. zdr y data_collection=deny
  cubren al modelo, no al buscador: quien llama debe enviar solo una consulta limpia, sin datos
  personales (core/internet.py). Las fuentes llegan en info["fuentes"] (url, título, extracto).
- Ver y oír (Fase 3): ver() manda imágenes al modelo de visión (NEXUS_OPENROUTER_MODEL_VISION) y
  transcribir() usa el endpoint /audio/transcriptions (NEXUS_OPENROUTER_MODEL_AUDIO), los dos con la
  misma política de datos, reintentos, presupuesto y registro de uso que el chat.
- Si OpenRouter no responde, lanza ChatUnavailable con una línea clara para el dueño y pausa el
  trabajo de fondo con espera creciente (paused_for). Ante 402 avisa al dueño una sola vez.

Nunca registra la key ni el contenido de los mensajes.
"""

import asyncio
import base64
import email.utils
import logging
import time
from typing import Protocol

import httpx

from config import settings

log = logging.getLogger("nexus.llm")

URL = "https://openrouter.ai/api/v1/chat/completions"
URL_AUDIO = "https://openrouter.ai/api/v1/audio/transcriptions"
TIMEOUT = 60
TIMEOUT_AUDIO = 180  # un tramo de 5 min de audio
RETRIES = 3  # reintentos tras el primer intento
BACKOFF = 1.0  # segundos; se duplica en cada reintento
MAX_INLINE_WAIT = 10  # un Retry-After más largo no se espera aquí: se avisa
PAUSE_MIN, PAUSE_MAX = 30, 1800  # pausa del trabajo de fondo tras un fallo (crece x2)
DATA_POLICY = {"zdr": True, "data_collection": "deny"}
WEB_PLUGIN = {"id": "web", "engine": "exa", "max_results": 5}

SAVED = "Lo que me envíe queda guardado y lo proceso después."
MESSAGES = {
    "sin_saldo": f"⚠ Me quedé sin saldo en OpenRouter. {SAVED}",
    "limite": f"⏳ OpenRouter me está limitando ahora mismo. {SAVED}",
    "clave": f"⚠ OpenRouter rechazó la clave (revisa OPENROUTER_API_KEYS en .env). {SAVED}",
    "caido": f"⚠ No pude conectar con OpenRouter. {SAVED}",
    "sin_config": f"⚠ Falta configurar OpenRouter (OPENROUTER_API_KEYS y NEXUS_OPENROUTER_MODEL en .env). {SAVED}",
    "sin_vision": f"⚠ Falta configurar el modelo de visión (NEXUS_OPENROUTER_MODEL_VISION en .env). {SAVED}",
    "sin_audio": f"⚠ Falta configurar el modelo de transcripción (NEXUS_OPENROUTER_MODEL_AUDIO en .env). {SAVED}",
}
NO_CREDIT_ALERT = "⚠ OpenRouter se quedó sin saldo. Recarga créditos; mientras tanto todo queda guardado."


class ChatUnavailable(Exception):
    """OpenRouter no puede responder ahora. str(exc) es el aviso para el dueño; `reason` es la
    causa ("presupuesto", "sin_saldo", "limite", "clave", "caido" o "sin_config")."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class Ledger(Protocol):
    """Registro de uso y avisos (core/usage.py)."""

    def record(self, info: dict) -> None: ...

    def spent_today(self) -> float: ...

    def alert_once(self, key: str, text: str) -> None: ...

    def clear_alert(self, key: str) -> None: ...


class _CallError(Exception):
    def __init__(self, status: int | None, retry_after: float | None = None):
        super().__init__(f"HTTP {status}" if status else "sin respuesta")
        self.status = status
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500

    @property
    def reason(self) -> str:
        if self.status == 402:
            return "sin_saldo"
        if self.status == 429:
            return "limite"
        if self.status in (401, 403):
            return "clave"
        return "caido"


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


class OpenRouter:
    def __init__(
        self, key: str, model: str, budget_usd: float | None, ledger: Ledger | None = None,
        transport: httpx.AsyncBaseTransport | None = None, deep_model: str = "", deep_fallback: str = "",
        vision_model: str = "", audio_model: str = "",
    ):
        self._key = key
        self.model = model
        self.vision_model = vision_model
        self.audio_model = audio_model
        self.deep_model = deep_model
        self.deep_fallback = deep_fallback
        self.budget = budget_usd
        self.ledger = ledger
        self._transport = transport
        self._failures = 0
        self._paused_until = 0.0
        self._alert_stale = True  # al primer éxito se borra un aviso de 402 ya resuelto
        self.last: dict = {"modelo": model, "ok": None, "error": None}

    # ---------- estado ----------

    def sano(self) -> bool:
        """True si la última llamada respondió (o aún no hubo fallos). La memoria de la charla, de
        prioridad más baja, solo corre si es así: nunca es la que prueba si OpenRouter volvió."""
        return self._failures == 0

    def paused_for(self) -> float:
        """Segundos que el trabajo de fondo debe esperar tras un fallo (0 = puede seguir)."""
        return max(0.0, self._paused_until - time.monotonic())

    def status(self) -> dict:
        return {
            "proveedor": "openrouter",
            "modelo": self.model,
            "modelo_profundo": self.deep_model or None,
            "modelo_profundo_respaldo": self.deep_fallback or None,
            "modelo_vision": self.vision_model or None,
            "modelo_audio": self.audio_model or None,
            "configurado": bool(self._key and self.model),
            "politica_datos": DATA_POLICY,
            "presupuesto_diario_usd": self.budget,
            "ultimo": self.last,
            "fallos_seguidos": self._failures,
            "pausa_s": int(self.paused_for()),
        }

    # ---------- llamada ----------

    def _over_budget(self) -> bool:
        if not self.budget or not self.ledger:
            return False
        try:
            return self.ledger.spent_today() >= self.budget
        except Exception as exc:
            log.warning("No pude leer el gasto de hoy (%s)", type(exc).__name__)
            return False

    async def _post(self, client: httpx.AsyncClient, body: dict, url: str = URL) -> dict:
        try:
            r = await client.post(url, headers={"Authorization": f"Bearer {self._key}"}, json=body)
        except httpx.HTTPError:
            raise _CallError(None) from None
        if r.status_code >= 400:  # sin el cuerpo: podría repetir parte del prompt
            raise _CallError(r.status_code, _retry_after(r.headers.get("retry-after")))
        try:
            data = r.json()
            if url == URL_AUDIO:
                if not isinstance(data["text"], str):
                    raise TypeError
            else:
                data["choices"][0]["message"]["content"]
            return data
        except (ValueError, KeyError, IndexError, TypeError):
            raise _CallError(502) from None

    async def _post_retrying(self, body: dict, url: str = URL) -> dict:
        timeout = TIMEOUT_AUDIO if url == URL_AUDIO else TIMEOUT
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            attempt = 0
            while True:
                try:
                    return await self._post(client, body, url)
                except _CallError as err:
                    wait = max(BACKOFF * 2**attempt, err.retry_after or 0)
                    if not err.transient or attempt >= RETRIES or wait > MAX_INLINE_WAIT:
                        raise
                    attempt += 1
                    log.info("OpenRouter: %s; reintento %d en %.0f s", err, attempt, wait)
                    await asyncio.sleep(wait)

    def _fail(self, err: _CallError) -> ChatUnavailable:
        self._failures += 1
        pause = min(PAUSE_MAX, PAUSE_MIN * 2 ** (self._failures - 1))
        self._paused_until = time.monotonic() + pause
        self.last = {"modelo": self.model, "ok": False, "error": str(err)}
        log.warning("OpenRouter no disponible (%s); trabajo de fondo en pausa %d s", err, pause)
        if err.reason == "sin_saldo" and self.ledger:
            self._alert_stale = True
            self._safe(self.ledger.alert_once, "sin_saldo", NO_CREDIT_ALERT)
        return ChatUnavailable(err.reason, MESSAGES[err.reason])

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:  # registrar o avisar nunca impide responder
            log.warning("Registro de uso no disponible (%s)", type(exc).__name__)

    def _listo(self, model: str, falta: str = "sin_config") -> None:
        """Lanza ChatUnavailable si falta la clave o el modelo, o si ya se alcanzó el tope del día."""
        if not self._key or not model:
            raise ChatUnavailable("sin_config", MESSAGES[falta])
        if self._over_budget():
            log.warning("Presupuesto diario alcanzado (%s USD): no gasto más hoy", self.budget)
            raise ChatUnavailable(
                "presupuesto", f"⏸ Llegué al tope de hoy ({self.budget} USD). {SAVED.replace('después', 'mañana')}"
            )

    def _exito(self, info: dict) -> None:
        if self._alert_stale and self.ledger:
            self._safe(self.ledger.clear_alert, "sin_saldo")
            self._alert_stale = False
        self._failures, self._paused_until = 0, 0.0
        self.last = {"modelo": info["modelo"], "ok": True, "error": None}
        if self.ledger:
            self._safe(self.ledger.record, info)

    async def chat_ex(
        self, messages: list[dict], *, model: str | None = None, temperature: float = 0,
        max_tokens: int | None = None, razonar: bool = False, web: bool = False, proveedor: str = "openrouter",
    ) -> tuple[str, dict]:
        """Devuelve (texto, uso). Sin `model`, el de uso diario. Temperatura 0 para datos y
        extracción; más alta para charla. Sin `razonar`, el razonamiento interno va apagado: con
        él, el modelo diario a veces gasta todo el tope de tokens pensando y no responde nada.
        Lanza ChatUnavailable si no se puede responder."""
        model = model or self.model
        self._listo(model)
        body = {
            "model": model, "messages": messages, "temperature": temperature,
            "usage": {"include": True}, "provider": DATA_POLICY,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if not razonar:
            body["reasoning"] = {"enabled": False}
        if web:
            body["plugins"] = [WEB_PLUGIN]
        modelos = [model]
        if model == self.deep_model and self.deep_fallback and self.deep_fallback != model:
            modelos.append(self.deep_fallback)
        for i, m in enumerate(modelos):
            body["model"] = m
            try:
                data = await self._post_retrying(body)
                break
            except _CallError as err:
                # Respaldo del potente: solo si está caído, limitado o no existe (404)
                if i + 1 < len(modelos) and err.reason in ("caido", "limite"):
                    log.warning("Modelo potente no disponible (%s); uso el de respaldo", err)
                    continue
                raise self._fail(err) from None
        u = data.get("usage") or {}
        cost = u.get("cost")
        info = {
            "proveedor": proveedor,
            "modelo": data.get("model") or model,
            "tokens_entrada": int(u.get("prompt_tokens") or 0),
            "tokens_salida": int(u.get("completion_tokens") or 0),
            "costo_usd": float(cost) if isinstance(cost, (int, float)) else None,
            "respaldo": body["model"] != model,
        }
        if web:  # en /usage aparte del chat; las fuentes, del formato url_citation
            info["proveedor"] = "openrouter-internet"
            info["fuentes"] = [
                {"url": c.get("url") or "", "titulo": c.get("title") or "", "extracto": c.get("content") or ""}
                for a in data["choices"][0]["message"].get("annotations") or []
                if a.get("type") == "url_citation" and isinstance(c := a.get("url_citation"), dict) and c.get("url")
            ]
        self._exito(info)
        return data["choices"][0]["message"]["content"] or "", info

    async def ver(self, prompt: str, imagenes: list[bytes], max_tokens: int = 900) -> tuple[str, dict]:
        """Una consulta al modelo de visión con una o varias imágenes JPEG (temperatura 0)."""
        self._listo(self.vision_model, "sin_vision")
        contenido = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}}
            for img in imagenes
        ]
        return await self.chat_ex([{"role": "user", "content": contenido}], model=self.vision_model,
                                  temperature=0, max_tokens=max_tokens, proveedor="openrouter-vision")

    async def transcribir(self, audio: bytes, formato: str, idioma: str = "es") -> tuple[dict, dict]:
        """Transcribe un audio (endpoint /audio/transcriptions, verbose_json con segmentos y su
        confianza). Devuelve (respuesta, uso). Lanza ChatUnavailable si no se puede."""
        self._listo(self.audio_model, "sin_audio")
        body = {
            "model": self.audio_model, "input_audio": {"data": base64.b64encode(audio).decode(), "format": formato},
            "language": idioma, "temperature": 0, "response_format": "verbose_json",
            "timestamp_granularities": ["segment"], "provider": DATA_POLICY,
        }
        try:
            data = await self._post_retrying(body, URL_AUDIO)
        except _CallError as err:
            raise self._fail(err) from None
        u = data.get("usage") or {}
        cost = u.get("cost")
        info = {
            "proveedor": "openrouter-audio", "modelo": self.audio_model, "tokens_entrada": 0, "tokens_salida": 0,
            "costo_usd": float(cost) if isinstance(cost, (int, float)) else None,
            "segundos": u.get("seconds") or data.get("duration"),
        }
        self._exito(info)
        return data, info

    async def chat(self, messages: list[dict], **kw) -> str:
        return (await self.chat_ex(messages, **kw))[0]


_client: OpenRouter | None = None


def get_client() -> OpenRouter:
    """Cliente único del proceso (conserva la pausa y los fallos entre peticiones)."""
    global _client
    if _client is None:
        from core import usage  # aquí para no crear un ciclo de importación

        _client = OpenRouter(
            settings.OPENROUTER_API_KEY, settings.OPENROUTER_MODEL, settings.DAILY_BUDGET_USD, usage,
            deep_model=settings.OPENROUTER_MODEL_DEEP, deep_fallback=settings.OPENROUTER_MODEL_DEEP_RESPALDO,
            vision_model=settings.OPENROUTER_MODEL_VISION, audio_model=settings.OPENROUTER_MODEL_AUDIO,
        )
        if not (_client._key and _client.model):
            log.warning("Falta OPENROUTER_API_KEYS o NEXUS_OPENROUTER_MODEL en .env: el chat no responderá")
    return _client
