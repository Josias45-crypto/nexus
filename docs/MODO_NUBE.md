# Modo nube (chat por OpenRouter)

> ⚠️ Con este modo encendido, las preguntas (`/ask`, `/chat`) y los textos no privados que se
> digieren se envían a OpenRouter. Lo privado nunca sale. Visión general de los modos y cómo
> pasar a local: [MODOS.md](MODOS.md).

Por defecto NEXUS es 100 % local (`NEXUS_MODE=local`). El modo nube existe para tener respuestas
de mejor calidad sin hardware potente (VPS de 8 GB sin GPU). Está **apagado por defecto**.

## Qué nunca sale de la máquina

- Los **embeddings** (vectores de búsqueda) y la **transcripción de audio** (Whisper).
- Los **originales**: archivos, audios e imágenes.
- Todo lo marcado como **privado** (`private=1`): si una respuesta o una digestión usa aunque
  sea un trozo privado, responde solo el modelo local, aunque la nube esté encendida.
- Los **recordatorios** (interpretar fechas) y el **resumen matutino**: siempre locales.

Para marcar algo como privado: `/privado` en Telegram, `"private": true` en `POST /inbox/text`
o `?private=true` en `POST /inbox/file`.

## Cómo encenderlo

En `.env` (nunca en el repositorio; edítalo con `nano .env`):

```env
NEXUS_MODE=cloud
OPENROUTER_API_KEYS=tu_key             # https://openrouter.ai/keys
NEXUS_OPENROUTER_MODEL=deepseek/deepseek-v4-flash
NEXUS_LOCAL_FALLBACK=off               # on = si la nube falla, responde Ollama local
NEXUS_DAILY_BUDGET_USD=0.50            # opcional: tope de gasto diario
NEXUS_SHOW_PROVIDER=off                # on = pie "NUBE: respondió ..."

# Opcional: otros proveedores compatibles, en cascada detrás de OpenRouter
NEXUS_CLOUD_ORDER=openrouter           # p. ej. openrouter,groq
GROQ_API_KEYS=
NEXUS_GROQ_MODEL=
GEMINI_API_KEYS=                       # de AI Studio (empieza por "AIza")
NEXUS_GEMINI_MODEL=
```

Aplica con `docker compose up -d`. Al arrancar, el registro muestra
`CHAT EN LA NUBE (openrouter)`. Si falta la key o el modelo de un proveedor, muestra un aviso y
ese proveedor queda fuera. Los modelos no tienen valor por defecto en el código: cambian seguido.
Para ver los vigentes desde el contenedor:

```bash
docker compose exec core python -c "import httpx; [print(m['id']) for m in httpx.get('https://openrouter.ai/api/v1/models').json()['data'] if m['id'].startswith('deepseek/')]"
```

## Cómo apagarlo

`NEXUS_MODE=local` (o `NEXUS_CHAT_BACKEND=ollama`) en `.env` y `docker compose up -d`. En modo
local hace falta un modelo de chat en Ollama (ver [MODOS.md](MODOS.md)). Puedes dejar las keys
escritas: con la nube apagada no se usan.

## Cómo decide quién responde

1. Si quien pregunta no admite nube (recordatorios, resumen) o el contexto es privado:
   Ollama local.
2. Si no, prueba los proveedores en el orden de `NEXUS_CLOUD_ORDER`. Dentro de cada uno,
   rota entre sus keys.
3. Si ya se gastó `NEXUS_DAILY_BUDGET_USD` hoy, no se llama a la nube (paso 6).
4. Ante **429** (límite), **5xx** (caída) o **timeout** (`NEXUS_CLOUD_TIMEOUT`, 30 s) se
   reintenta la misma key `NEXUS_CLOUD_RETRIES` veces (2) con espera creciente (1 s, 2 s; o lo
   que diga `Retry-After`, si es de 10 s o menos). Si sigue fallando, la key se **enfría** como
   mínimo `NEXUS_CLOUD_COOLDOWN` (60 s) y se prueba la siguiente key y el siguiente proveedor.
5. **402** (sin saldo) y otros 4xx (modelo inexistente) enfrían la key 10 minutos sin
   reintentar; una key rechazada (401/403), 1 hora.
6. Si nada responde: con `NEXUS_LOCAL_FALLBACK=on`, **Ollama local**; con `off`, un aviso claro
   al usuario ("⚠ No pude responder: la cuenta de la nube no tiene saldo (402)…").

## Cómo saber quién respondió y cuánto costó

- Con `NEXUS_SHOW_PROVIDER=on`, la interfaz web muestra una insignia roja **NUBE** junto a la
  respuesta (al pasar el cursor: proveedor, modelo y key) y en Telegram la respuesta termina
  con `☁️ NUBE: respondió <proveedor> (<modelo>)`.
- `GET /usage?days=30`: peticiones, tokens y costo en USD por día y modelo, lo gastado hoy y el
  presupuesto (tabla `llm_usage`; nunca el contenido).
- `GET /health` y `GET /worker`, en `llm`, muestran:
  - `ultimo`: proveedor, modelo, si fue nube y qué key (`key#1`). Si respondió el local,
    `motivo_local` explica por qué (`contenido privado`, `nube no disponible: …`,
    `presupuesto diario agotado`).
  - `nube.proveedores`: el estado de cada key (`lista` o `enfriando N s`, usos, fallos y
    último error), siempre como `key#n`. **Nunca se muestra ni se registra el valor de una
    key ni el contenido de los prompts.**
- En `/knowledge`, el campo `model` de cada resumen dice quién lo hizo (p. ej.
  `groq:<modelo>` u `ollama:qwen2.5:1.5b`).

## Advertencia sobre cuentas y cuotas gratuitas

Abrir **varias cuentas para multiplicar las cuotas gratuitas puede violar los términos de
servicio** de los proveedores y arriesga el **bloqueo** de todas ellas. Recomendación: **una key
por proveedor**. Varias keys de un mismo proveedor solo tienen sentido si son legítimas (por
ejemplo, varios proyectos de pago de la misma organización, si los términos lo permiten).

## Pruebas

`python3 tests/e2e.py` arranca la instancia aislada en modo cloud con **keys falsas**, una URL
local que rechaza la conexión y respaldo local encendido. Comprueba que todo cae al local sin
romperse, que un documento privado se responde solo en local, que `/usage` registra las
peticiones y que ninguna key aparece en los registros. `tests/test_cloud.py` simula, sin tocar
la red, 429 con y sin `Retry-After` (reintentos), 5xx, 401, 402 con respaldo encendido y
apagado, el presupuesto diario y una pregunta privada sin modelo local.
