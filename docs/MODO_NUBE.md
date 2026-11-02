# Chat por OpenRouter

NEXUS conversa, resume y redacta solo con OpenRouter (`providers/openrouter.py`). No hay modelo
de chat local ni respaldo. Embeddings, audio y OCR siguen en la máquina.

## Configurar

En `.env` (la clave solo ahí, nunca en el repositorio):

```env
OPENROUTER_API_KEYS=<tu clave>
NEXUS_OPENROUTER_MODEL=deepseek/deepseek-v4-flash
NEXUS_DAILY_BUDGET_USD=0.50     # opcional; vacío = sin tope
```

Luego `docker compose up -d`. `GET /health` muestra el modelo, la política de datos, el último
resultado, los fallos seguidos y la pausa del trabajo de fondo.

## Política de datos

Cada petición lleva `provider: {"zdr": true, "data_collection": "deny"}`: OpenRouter solo usa
proveedores con **retención cero** que **no guardan ni entrenan** con tus datos
([documentación](https://openrouter.ai/docs/features/provider-routing)). Está fijo en el código.

Probado el 2026-11-02 con `deepseek/deepseek-v4-flash`: respondió (proveedor Wafer). La mayoría
de los proveedores del modelo cumplen (DeepInfra, Relace, Parasail, Wafer, Azure...), y entre
ellos están los más baratos (~0,05–0,09 USD por millón de tokens de entrada y ~0,14–0,45 USD de
salida), así que el precio prácticamente no cambia. Quedan fuera StreamLake, GMICloud, Baidu,
AtlasCloud y Alibaba.

## Qué sale de la máquina

La pregunta, los fragmentos de memoria que sirven para responderla, los textos a resumir y los
datos del saludo matutino. Nunca: embeddings, audio, OCR, la clave ni los registros.

## Si OpenRouter falla

| Situación | Qué pasa |
|---|---|
| 429, 5xx, timeout o red | Reintenta con espera creciente (1, 2 y 4 s). Si sigue, avisa |
| 402 (sin saldo) | Avisa en una línea; por Telegram te avisa **una sola vez** hasta que vuelva a responder |
| 401/403 (clave) | Avisa que revises `OPENROUTER_API_KEYS` |
| Tope diario alcanzado | «⏸ Llegué al tope de hoy»: no llama a OpenRouter hasta mañana |

En todos los casos **no se pierde nada**: el original ya está guardado, el troceado y los
embeddings son locales y la digestión queda en cola (`retry`) sin gastar intentos. El trabajo de
fondo espera una pausa que crece con cada fallo (30 s, 60 s... hasta 30 min) y sigue solo cuando
OpenRouter vuelve.

## Costo

`GET /usage?days=30`: peticiones, tokens y costo por día (lo informa OpenRouter en cada
respuesta), `gastado_hoy_usd` y `presupuesto_diario_usd`. En `tests/eval_ventas.py` cada
pregunta cuesta unos 0,0001 USD.
