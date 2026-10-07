# API

Base: `http://localhost:8000`. La documentación interactiva con todos los campos está en
<http://localhost:8000/docs>. Los errores llegan en español como `{"detail": "..."}`; los de
validación (422) además traen `errores: [{campo, problema}]`.

No hay autenticación: la API escucha solo en `127.0.0.1` y se accede por Tailscale o SSH.

## Estado

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | `status` `ok` o `degradado`; si Ollama responde y tiene los modelos; `llm_activo`; `nube` (si la última respuesta salió de la nube). 503 si la base no responde |
| GET | `/profile` | Perfil activo: nombre del asistente, negocio, categorías, reglas proactivas, ejemplos |
| GET | `/worker` | Estado de los bucles, digestión en curso (`digiriendo`) y eventos por estado |
| GET | `/growth?days=14` | Días de vida, racha, elementos aprendidos, pendientes y fallidos |
| GET | `/stats` | Elementos por tipo y pendientes |

## Enseñar (bandeja)

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/inbox/text` | `{"text": "...", "source": "api", "private": false}` guarda una nota |
| POST | `/inbox/file?source=api&private=false` | Sube un archivo (multipart, campo `file`). Límite: `NEXUS_MAX_UPLOAD_MB` (413 si lo supera) |
| GET | `/inbox?limit=20` | Últimos elementos: `status`, `error` (motivo), `attempts`, `digest`, `procesando`, `reintentable` |

Respuesta de la bandeja: `{"id", "kind", "duplicate", ...}`. Si el contenido ya existía,
`duplicate: true` y no se guarda dos veces.

Estados de un elemento: `pending` → `processed` | `empty` (sin texto) | `unsupported` (formato
no legible, con motivo) | `failed` (tras `NEXUS_MAX_ATTEMPTS`).

```bash
curl -X POST localhost:8000/inbox/text -H 'Content-Type: application/json' \
  -d '{"text":"Rosa Paredes debe 120 soles desde el lunes"}'
curl -F file=@ventas.xlsx 'localhost:8000/inbox/file?source=api'
```

## Preguntar y buscar

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/ask` | `{"question": "..."}` responde con la memoria. Devuelve `answer`, `sources` (`n`, `cita`, `filename`, `snippet`, `distance`), `llm` (proveedor, modelo, `nube`, `regenerada`) y `sin_verificar`: nombres o cifras de la respuesta que no están en las fuentes (también aparecen al final como «⚠ No pude verificar: …») |
| GET | `/search?q=...&k=5` | Búsqueda híbrida sin redactar respuesta: trozos con `event_id`, `meta`, `distance` |
| POST | `/chat` | `{"message": "..."}` habla con el modelo **sin** memoria |
| GET | `/knowledge?limit=20` | Resúmenes y conceptos generados |
| GET | `/today?dia=AAAA-MM-DD` | Lo que aprendió ese día (hoy por defecto) y los recordatorios del día |

```bash
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"¿Cuánto me debe Rosa?"}'
```

## Procesamiento y mantenimiento

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/process?limit=10` | Procesa ya lo pendiente (el worker lo hace solo cada `NEXUS_WORKER_INTERVAL` s) |
| POST | `/requeue?estado=failed` | Reencola todos los elementos en `failed`, `unsupported`, `empty` o `todos` |
| POST | `/requeue/{id}` | Reencola uno. 409 si no está en un estado reintentable |
| POST | `/requeue/digests` | Reintenta los resúmenes fallidos |
| POST | `/reindex?event_id=...` | Rehace trozos, vectores y resumen de uno desde su original |
| POST | `/reindex?todos=true` | Rehace todos (tras cambiar de modelo de embeddings o de troceado) |
| POST | `/backup` | Respaldo inmediato y verificado |
| GET | `/backup` | Respaldos existentes y el último |

## Recordatorios y avisos

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/reminders/parse` | `{"text": "recuérdame mañana a las 9 llamar a Pedro"}` interpreta **sin guardar** (422 si no entiende la fecha) |
| POST | `/reminders` | `{"text", "due_at", "channel", "chat_id"}` guarda (tras confirmar con el usuario) |
| GET | `/reminders?chat_id=` | Recordatorios abiertos |
| POST | `/reminders/done?chat_id=` | Marca hecho el último avisado |
| POST | `/reminders/{id}/done` · `/reminders/{id}/cancel` | Marca hecho o cancela |
| GET | `/briefing` | Vista previa del resumen matutino (no envía) |
| POST | `/briefing/send?channel=telegram` | Envía el resumen ahora |
| GET | `/outbox?channel=telegram` | Avisos pendientes para un canal (los canales los recogen) |
| POST | `/outbox/{id}/delivered` | El canal confirma la entrega |

## Páginas

| Ruta | Qué es |
|---|---|
| `/` | Conversación, subir archivos, grabar voz y **Bandeja**: estado de cada elemento, motivo y botón Reintentar |
| `/brain` | Grafo en vivo de recuerdos y conceptos; clic en un punto para ver su detalle y reintentarlo |
| `/brain/graph` | Datos del grafo (JSON) |
| `/brain/stream` | Eventos en vivo (SSE): `ingest`, `processing`, `processed`, `processing_error`, `digested`, `digest_progress`, `recall`, `backup` |
| `/dashboard` | Crecimiento y respaldos |
