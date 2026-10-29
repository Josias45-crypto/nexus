"""Cliente de chat para APIs compatibles con OpenAI (OpenRouter, Groq, Gemini en AI Studio)."""

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

    def user_message(self) -> str:
        """Explicación para el dueño (sin datos técnicos ni secretos)."""
        if self.status == 402:
            return "la cuenta de la nube no tiene saldo (402): recarga créditos en OpenRouter"
        if self.status == 429:
            return "la nube está limitando las peticiones (429): prueba en unos minutos"
        if self.status in (401, 403):
            return f"la nube rechazó la key ({self.status}): revisa la key en .env"
        if self.status is None:
            return f"no hubo respuesta de la nube ({self})"
        return f"la nube respondió con un error ({self})"


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


def _usage(data: dict) -> dict:
    """Tokens y costo (OpenRouter devuelve usage.cost en USD; otros, solo tokens)."""
    u = data.get("usage") or {}
    cost = u.get("cost")
    return {
        "tokens_entrada": int(u.get("prompt_tokens") or 0),
        "tokens_salida": int(u.get("completion_tokens") or 0),
        "costo_usd": float(cost) if isinstance(cost, (int, float)) else None,
    }


async def chat(
    client: httpx.AsyncClient, base_url: str, api_key: str, model: str, messages: list[dict],
    extra: dict | None = None,
) -> tuple[str, dict]:
    """Devuelve (texto, uso). `extra` agrega campos propios del proveedor al cuerpo."""
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, **(extra or {})},
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
        data = r.json()
        return data["choices"][0]["message"]["content"] or "", _usage(data)
    except (ValueError, KeyError, IndexError, TypeError):
        raise CloudCallError("respuesta con formato inesperado", r.status_code) from None
