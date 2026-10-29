# Modos: nube y local

NEXUS separa **qué** sabe (la memoria, siempre en tu máquina) de **quién** redacta las
respuestas (el modelo de chat, intercambiable por configuración). `NEXUS_MODE` fija los valores
por defecto y cada capacidad se puede cambiar por separado.

| Capacidad | Variable | `cloud` | `local` |
|---|---|---|---|
| Chat (/ask, /chat, digestión) | `NEXUS_CHAT_BACKEND` | `openrouter` | `ollama` |
| Audio (transcripción) | `NEXUS_AUDIO_BACKEND` | `local` (faster-whisper) | `local` |
| Visión (imágenes, PDF escaneados) | `NEXUS_VISION_BACKEND` | `ocr` (Tesseract) | `ocr` |
| Embeddings (búsqueda) | `NEXUS_EMBED_BACKEND` | `local` (nomic-embed-text) | `local` |
| Respaldo si la nube falla | `NEXUS_LOCAL_FALLBACK` | `off` | `on` |
| Pie "NUBE: respondió …" | `NEXUS_SHOW_PROVIDER` | `off` | `on` |
| Modelos que descarga Ollama | `NEXUS_OLLAMA_MODELS` | `nomic-embed-text` | `nomic-embed-text,<chat>` |

Hoy solo el chat puede ir a la nube. `openrouter` en audio y visión está reservado para más
adelante y no hace nada todavía. Sin `NEXUS_MODE`, el valor por defecto es `local`, con la nube
apagada; `NEXUS_ALLOW_CLOUD=on` de versiones anteriores equivale a `cloud`.

## Modo nube (hoy)

Pensado para el VPS básico (8 GB, sin GPU): el chat lo hace un modelo grande de OpenRouter y la
máquina solo carga los embeddings, Whisper y Tesseract.

```env
NEXUS_MODE=cloud
OPENROUTER_API_KEYS=<tu key>             # solo en .env; edítalo con nano
NEXUS_OPENROUTER_MODEL=deepseek/deepseek-v4-flash
NEXUS_LOCAL_FALLBACK=off                 # el VPS no tiene modelo de chat local
NEXUS_DAILY_BUDGET_USD=0.50              # opcional
```

Aplica con `docker compose up -d`. El contenedor `ollama-models` descarga solo
`nomic-embed-text`. Si ya habías descargado otro modelo, sigue en el volumen hasta que lo borres
(`docker exec nexus-ollama ollama rm qwen2.5:1.5b`).

**Si OpenRouter falla** (tras reintentar 429/5xx/timeout con espera creciente):
- `NEXUS_LOCAL_FALLBACK=on`: responde el Ollama local (necesita un modelo de chat descargado).
- `NEXUS_LOCAL_FALLBACK=off`: el usuario recibe un aviso claro, por ejemplo
  "⚠ No pude responder: la cuenta de la nube no tiene saldo (402): recarga créditos en
  OpenRouter…". La digestión pendiente se reintenta más tarde; no se guarda un resumen vacío.

**Costo.** Cada petición al modelo de chat queda en la tabla `llm_usage` (proveedor, modelo,
tokens y costo en USD que informa OpenRouter; nunca el contenido). `GET /usage?days=30` da los
totales por día. Con `NEXUS_DAILY_BUDGET_USD`, al alcanzar el tope del día NEXUS deja de usar la
nube: responde el respaldo local (si está en `on`) o avisa.

**Recordatorios y resumen matutino** usan solo el modelo local, como siempre. Sin modelo de chat
local, los recordatorios se interpretan con las reglas de `core/when.py` y el saludo del
resumen es una frase fija.

## Pasar a modo local (con mejor hardware)

El conocimiento no cambia: la memoria, los embeddings y los originales ya están en tu máquina.
Solo cambia quién redacta.

**Hardware orientativo (CPU sin GPU es lento para modelos grandes):**

| Modelo de chat (Ollama) | RAM/VRAM | Comentario |
|---|---|---|
| `qwen2.5:1.5b` | 4 GB RAM | Funciona en CPU, pero sintetiza peor e inventa más (ver `tests/eval_ventas.py`) |
| `qwen2.5:7b` | 8 GB VRAM o 16 GB RAM | Calidad aceptable; en CPU, decenas de segundos por respuesta |
| `qwen2.5:14b` | 12-16 GB VRAM | Recomendado con GPU (RTX 3060 12 GB o superior) |

**Variables:**

```env
NEXUS_MODE=local
NEXUS_LLM_MODEL=qwen2.5:7b
# opcional: NEXUS_OLLAMA_MODELS=nomic-embed-text,qwen2.5:7b
```

**Modelos:** `docker compose up -d` descarga lo que falte (`NEXUS_LLM_MODEL` y
`nomic-embed-text`). Para hacerlo a mano: `docker exec nexus-ollama ollama pull qwen2.5:7b`.

También puedes mezclar: modo `cloud` con `NEXUS_LOCAL_FALLBACK=on` y un modelo local pequeño
como respaldo y para lo privado, o un Ollama con GPU en otra máquina por Tailscale
(`NEXUS_LLM_URL`).

Antes y después de cambiar de modelo, mide con
`python3 tests/eval_ventas.py --label <nombre>` (local) o
`python3 tests/eval_ventas.py --nube --orden openrouter --label <nombre>` (nube).

## Privacidad

- **Nunca salen de la máquina:** embeddings, audio, OCR, visión, los originales, los
  recordatorios, el resumen matutino y todo lo marcado como privado.
- **Lo privado** (`/privado` en Telegram, `"private": true` en `/inbox/text`): si una respuesta
  o una digestión usa aunque sea un trozo privado, responde solo el modelo local. Si no hay
  modelo local disponible (VPS en modo cloud), NEXUS responde "🔒 Esto toca notas privadas y no
  hay un modelo local disponible para responder. No lo envié a la nube.".
- **Lo que sí sale en modo cloud:** las preguntas no privadas y los trozos de memoria que se usan
  para responderlas, y los textos no privados que se resumen. Si eso no es aceptable para un
  dato, márcalo como privado.
- Las keys solo viven en `.env`. Nunca se registran ni se muestran (en el estado aparecen como
  `key#1`), y los prompts tampoco se registran.
