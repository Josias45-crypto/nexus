"""Enrutador de modelos: decide quién responde cada petición.

- Con NEXUS_CHAT_BACKEND=ollama, Ollama local (o el remoto de NEXUS_LLM_URL con respaldo local).
- La nube (OpenRouter) solo si quien llama la pide (cloud=True; hoy: /ask, /chat y digestión),
  NEXUS_CHAT_BACKEND=openrouter, el contenido NO es privado y no se superó el presupuesto diario.
- Si la nube falla: con NEXUS_LOCAL_FALLBACK=on responde el local; con off, ChatUnavailable con
  un aviso claro para el dueño.
- Lo privado usa solo el local; si no hay modelo local, ChatUnavailable (nunca se envía).
"""

import logging
from typing import Protocol

import httpx

from config import settings
from providers.base import ChatUnavailable, LLMProvider, ProviderUnavailable
from providers.cloud import CloudPool
from providers.fallback import FallbackProvider
from providers.ollama import OllamaProvider

log = logging.getLogger("nexus.llm")


class Ledger(Protocol):
    """Registro de uso (core/usage.py)."""

    def record(self, info: dict) -> None: ...

    def spent_today(self) -> float: ...


class Router:
    def __init__(
        self, local: LLMProvider, local_model: str, pool: CloudPool | None, cloud_notes: list[str],
        ledger: Ledger | None = None,
    ):
        self.local = local
        self.local_model = local_model
        self.pool = pool
        self.cloud_notes = cloud_notes
        self.ledger = ledger
        self.last: dict = {"proveedor": "ollama", "modelo": local_model, "nube": False, "key": None}

    def _record(self, info: dict) -> None:
        if self.ledger:
            try:
                self.ledger.record(info)
            except Exception as exc:  # el registro nunca impide responder
                log.warning("No pude registrar el uso del LLM (%s)", type(exc).__name__)

    def _over_budget(self) -> float | None:
        """Devuelve el tope si ya se gastó hoy; None si queda presupuesto o no hay tope."""
        budget = settings.DAILY_BUDGET_USD
        if not budget or not self.ledger:
            return None
        try:
            return budget if self.ledger.spent_today() >= budget else None
        except Exception as exc:
            log.warning("No pude leer el gasto de hoy (%s)", type(exc).__name__)
            return None

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.pool and self.pool.providers)

    async def chat_ex(
        self, messages: list[dict], *, cloud: bool = False, private: bool = False
    ) -> tuple[str, dict]:
        """Devuelve (texto, quién respondió). Nunca registra el contenido.
        Lanza ChatUnavailable (con el aviso para el dueño) si nadie puede responder."""
        reason = None
        if cloud and self.cloud_enabled:
            if private:
                reason = "contenido privado"
            else:
                budget = self._over_budget()
                if budget is not None:
                    reason = f"presupuesto diario agotado ({budget} USD)"
                    notice = (
                        f"se alcanzó el presupuesto diario de la nube ({budget} USD, "
                        "NEXUS_DAILY_BUDGET_USD); no gasto más hasta mañana"
                    )
                    log.warning("Presupuesto diario de la nube agotado (%s USD)", budget)
                else:
                    try:
                        text, info = await self.pool.chat(messages)
                        self.last = info
                        self._record(info)
                        log.info("LLM respondió: %s (%s, %s)", info["proveedor"], info["modelo"], info["key"])
                        return text, info
                    except ProviderUnavailable as exc:
                        reason = f"nube no disponible: {exc}"
                        notice = exc.user_message
                        log.warning("Nube no disponible (%s)", exc)
                if not settings.LOCAL_FALLBACK:
                    raise ChatUnavailable(
                        f"⚠ No pude responder: {notice}. El respaldo local está apagado "
                        "(NEXUS_LOCAL_FALLBACK=off)."
                    )
        try:
            text = await self.local.chat(messages)
        except httpx.HTTPError as exc:
            log.warning("Modelo local no disponible (%s)", type(exc).__name__)
            if private:
                raise ChatUnavailable(
                    "🔒 Esto toca notas privadas y no hay un modelo local disponible para "
                    "responder. No lo envié a la nube."
                ) from None
            raise ChatUnavailable(
                "⚠ No pude responder: el modelo local no está disponible"
                + (f" y {reason}." if reason else ".")
            ) from None
        name = getattr(self.local, "last_name", "ollama")
        info = {"proveedor": name, "modelo": self.local_model, "nube": False, "key": None}
        if reason:
            info["motivo_local"] = reason
        self.last = info
        self._record(info)
        return text, info

    async def chat(self, messages: list[dict], *, cloud: bool = False, private: bool = False) -> str:
        return (await self.chat_ex(messages, cloud=cloud, private=private))[0]

    def status(self) -> dict:
        return {
            "ultimo": self.last,
            "modo": settings.MODE,
            "backends": {
                "chat": settings.CHAT_BACKEND, "audio": settings.AUDIO_BACKEND,
                "vision": settings.VISION_BACKEND, "embeddings": settings.EMBED_BACKEND,
            },
            "respaldo_local": settings.LOCAL_FALLBACK,
            "presupuesto_diario_usd": settings.DAILY_BUDGET_USD,
            "nube": {
                "permitida": settings.ALLOW_CLOUD,
                "activa": self.cloud_enabled,
                "orden": [p.name for p in self.pool.providers] if self.pool else [],
                "proveedores": self.pool.status() if self.pool else {},
                "avisos": self.cloud_notes,
            },
        }


def build_router(ledger: Ledger | None = None) -> Router:
    local: LLMProvider = OllamaProvider(settings.OLLAMA_BASE_URL, settings.LLM_MODEL)
    if settings.LLM_URL:
        remote = OllamaProvider(settings.LLM_URL, settings.LLM_REMOTE_MODEL, connect_timeout=3)
        local = FallbackProvider(remote, local, name="ollama-remoto")
    pool, notes = None, []
    if settings.ALLOW_CLOUD:
        pool, notes = CloudPool.from_settings()
        for n in notes:
            log.warning("Modo nube: %s", n)
        if pool.providers:
            log.warning(
                "CHAT EN LA NUBE (%s): preguntas y digestión no privadas salen a la nube; lo "
                "privado, embeddings, audio y OCR se quedan aquí. Respaldo local: %s.",
                ", ".join(p.name for p in pool.providers),
                "on" if settings.LOCAL_FALLBACK else "off",
            )
    return Router(local, settings.LLM_MODEL, pool, notes, ledger)
