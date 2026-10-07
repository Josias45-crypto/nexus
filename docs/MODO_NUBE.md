# Modo nube de pruebas

> ⚠️ **Solo para probar con datos NO sensibles.** Con este modo encendido, las preguntas
> (`/ask`, `/chat`) y los textos que se digieren pueden enviarse a Groq, Gemini u OpenRouter.
> En producción, con datos reales: `NEXUS_ALLOW_CLOUD=off`.

Por defecto NEXUS es 100 % local. El modo nube existe para probar respuestas más rápidas o de
mejor calidad sin comprar hardware. Está **apagado por defecto**.

## Qué nunca sale de la máquina

- Los **embeddings** (vectores de búsqueda) y la **transcripción de audio** (Whisper).
- Los **originales**: archivos, audios e imágenes.
- Todo lo marcado como **privado** (`private=1`): si una respuesta o una digestión usa aunque
  sea un trozo privado, responde solo el modelo local, aunque la nube esté encendida.
- Los **recordatorios** (interpretar fechas) y el **resumen matutino**: siempre locales.

Para marcar algo como privado: `/privado` en Telegram, `"private": true` en `POST /inbox/text`
o `?private=true` en `POST /inbox/file`.

## Cómo encenderlo

En `.env` (nunca en el repositorio):

```env
NEXUS_ALLOW_CLOUD=on
NEXUS_CLOUD_ORDER=groq,gemini          # orden de preferencia

GROQ_API_KEYS=tu_key_de_groq           # https://console.groq.com
NEXUS_GROQ_MODEL=openai/gpt-oss-20b    # probado: estable, ~1 s por respuesta, buen español

GEMINI_API_KEYS=tu_key_de_gemini       # https://aistudio.google.com (Get API key)
NEXUS_GEMINI_MODEL=<modelo de Gemini>  # p. ej. un modelo "flash" vigente
# La key de Gemini debe ser de AI Studio (empieza por "AIza"); otra da HTTP 401.

# Opcional
OPENROUTER_API_KEYS=
NEXUS_OPENROUTER_MODEL=
```

Aplica con `docker compose up -d core`. Al arrancar, el registro muestra
`MODO NUBE DE PRUEBAS ACTIVO (groq, gemini)`. Si falta la key o el modelo de un proveedor,
muestra un aviso y ese proveedor queda fuera. Los modelos no tienen valor por defecto en el
código: cambian seguido, así que elige uno vigente en la consola de cada proveedor.

## Cómo apagarlo

`NEXUS_ALLOW_CLOUD=off` en `.env` y `docker compose up -d core`. Puedes dejar las keys
escritas: con el modo apagado no se usan.

## Cómo decide quién responde

1. Si quien pregunta no admite nube (recordatorios, resumen) o el contexto es privado:
   Ollama local.
2. Si no, prueba los proveedores en el orden de `NEXUS_CLOUD_ORDER`. Dentro de cada uno,
   rota entre sus keys.
3. Ante **429** (límite), **5xx** (caída) o **timeout** (`NEXUS_CLOUD_TIMEOUT`, 30 s), esa key
   se **enfría**: no se vuelve a usar hasta que pase lo que diga `Retry-After`, como mínimo
   `NEXUS_CLOUD_COOLDOWN` (60 s). Se prueba la siguiente key y luego el siguiente proveedor.
4. Una key rechazada (401/403) se enfría 1 hora. Otros errores 4xx (por ejemplo, un modelo
   inexistente), 10 minutos.
5. Si nada responde: **Ollama local**, sin error para el usuario.

## Cómo saber quién respondió

- La interfaz web muestra una insignia roja **NUBE** junto a la respuesta. Al pasar el
  cursor ves el proveedor, el modelo y la key.
- En Telegram, la respuesta termina con `☁️ NUBE: respondió <proveedor>`.
- `GET /health` y `GET /worker`, en `llm`, muestran:
  - `ultimo`: proveedor, modelo, si fue nube y qué key (`key#1`). Si respondió el local,
    `motivo_local` explica por qué (`contenido privado`, `nube no disponible: …`).
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

`python3 tests/e2e.py` arranca la instancia aislada con el modo nube encendido, **keys falsas**
y una URL local que rechaza la conexión. Comprueba que todo cae al local sin romperse, que un
documento privado se responde solo en local y que ninguna key aparece en los registros.
`tests/test_cloud.py` simula 429 con `Retry-After`, 5xx y 401 sin tocar la red.
