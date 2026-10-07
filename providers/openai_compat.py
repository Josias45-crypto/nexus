"""Cliente de chat para APIs compatibles con OpenAI (Groq, Gemini en AI Studio, OpenRouter)."""

import email.utils
import time

import httpx


class CloudCallError(Exception):
    """Fallo de una key o proveedor. Nunca incluye la key ni el contenido del prompt."""

    def __init__(self, reason: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(reason)
        self.status = status
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        """429, 5xx, timeout o red: vale la pena reintentar más tarde."""
        return self.status is None or self.status == 429 or self.status >= 500


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
            return max(0.0, when.timestamp() - time.time())
        except (TypeError, ValueError):
            return None


async def chat(
    client: httpx.AsyncClient, base_url: str, api_key: str, model: str, messages: list[dict]
) -> str:
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages},
        )
    except httpx.TimeoutException:
        raise CloudCallError("tiempo de espera agotado") from None
    except httpx.HTTPError as exc:
        raise CloudCallError(f"sin conexión ({type(exc).__name__})") from None
    if r.status_code >= 400:
        # Sin el cuerpo: podría repetir parte del prompt
        raise CloudCallError(
            f"HTTP {r.status_code}", r.status_code, _retry_after(r.headers.get("retry-after"))
        )
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise CloudCallError("respuesta con formato inesperado", r.status_code) from None
