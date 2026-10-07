"""Pool de proveedores en la nube (modo de pruebas).

Orden configurable (NEXUS_CLOUD_ORDER). En cada proveedor se rota entre sus keys; ante 429,
5xx o timeout la key se enfría (Retry-After, mínimo NEXUS_CLOUD_COOLDOWN s) y se prueba la
siguiente key, luego el siguiente proveedor. Si nada responde, quien llama usa Ollama local.

Nunca se registran keys ni prompts: las keys se nombran "key#1", "key#2"...
"""

import logging
import time
from dataclasses import dataclass, field

import httpx

from config import settings
from providers import openai_compat
from providers.base import ProviderUnavailable

log = logging.getLogger("nexus.cloud")

REJECTED_COOLDOWN = 3600  # key rechazada (401/403): no reintentar en una hora
CONFIG_COOLDOWN = 600  # otros 4xx (modelo inexistente, petición inválida)


@dataclass
class KeyState:
    label: str
    secret: str = field(repr=False)
    cooldown_until: float = 0.0
    uses: int = 0
    failures: int = 0
    last_error: str | None = None

    def status(self, now: float) -> dict:
        wait = self.cooldown_until - now
        return {
            "key": self.label,
            "estado": f"enfriando {int(wait)} s" if wait > 0 else "lista",
            "usos": self.uses,
            "fallos": self.failures,
            "ultimo_error": self.last_error,
        }


@dataclass
class CloudProvider:
    name: str
    model: str
    url: str
    keys: list[KeyState]
    next_index: int = 0

    def rotation(self) -> list[KeyState]:
        """Empieza por la key siguiente a la última usada (reparte la carga)."""
        n = len(self.keys)
        order = [self.keys[(self.next_index + i) % n] for i in range(n)]
        self.next_index = (self.next_index + 1) % n
        return order


class CloudPool:
    def __init__(self, providers: list[CloudProvider], transport: httpx.AsyncBaseTransport | None = None):
        self.providers = providers
        self._transport = transport

    @classmethod
    def from_settings(cls) -> tuple["CloudPool", list[str]]:
        """Devuelve el pool y los avisos de configuración (sin datos secretos)."""
        providers, notes = [], []
        for name in settings.CLOUD_ORDER:
            cfg = settings.CLOUD_PROVIDERS.get(name)
            if cfg is None:
                notes.append(f"{name}: proveedor desconocido (válidos: groq, gemini, openrouter)")
                continue
            if not cfg["keys"]:
                notes.append(f"{name}: sin keys ({name.upper()}_API_KEYS)")
                continue
            if not cfg["model"]:
                notes.append(f"{name}: falta el modelo (NEXUS_{name.upper()}_MODEL)")
                continue
            keys = [KeyState(f"key#{i}", k) for i, k in enumerate(cfg["keys"], 1)]
            providers.append(CloudProvider(name, cfg["model"], cfg["url"], keys))
        return cls(providers), notes

    def available(self) -> bool:
        now = time.monotonic()
        return any(k.cooldown_until <= now for p in self.providers for k in p.keys)

    def _cool(self, p: CloudProvider, k: KeyState, err: openai_compat.CloudCallError) -> None:
        if err.transient:
            wait = max(settings.CLOUD_MIN_COOLDOWN, err.retry_after or 0)
        elif err.status in (401, 403):
            wait = REJECTED_COOLDOWN
        else:
            wait = CONFIG_COOLDOWN
        k.cooldown_until = time.monotonic() + wait
        k.failures += 1
        k.last_error = str(err)
        log.warning("Nube %s %s: %s; enfriando %d s", p.name, k.label, err, wait)

    async def chat(self, messages: list[dict]) -> tuple[str, dict]:
        tried = 0
        async with httpx.AsyncClient(timeout=settings.CLOUD_TIMEOUT, transport=self._transport) as client:
            for p in self.providers:
                for k in p.rotation():
                    if k.cooldown_until > time.monotonic():
                        continue
                    tried += 1
                    try:
                        text = await openai_compat.chat(client, p.url, k.secret, p.model, messages)
                    except openai_compat.CloudCallError as err:
                        self._cool(p, k, err)
                        continue
                    k.uses += 1
                    k.last_error = None
                    return text, {"proveedor": p.name, "modelo": p.model, "nube": True, "key": k.label}
        raise ProviderUnavailable(
            "ninguna key disponible" if tried == 0 else f"fallaron {tried} intento(s) en la nube"
        )

    def status(self) -> dict:
        now = time.monotonic()
        return {
            p.name: {"modelo": p.model, "keys": [k.status(now) for k in p.keys]}
            for p in self.providers
        }
