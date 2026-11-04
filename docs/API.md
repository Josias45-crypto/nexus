# API

Base: `http://localhost:8000`. La documentación interactiva con todos los campos está en
<http://localhost:8000/docs>. Los errores llegan en español como `{"detail": "..."}`; los de
validación (422) además traen `errores: [{campo, problema}]`.

Autenticación (Fase 5): toda ruta exige la cookie de sesión de `POST /login` (formulario `clave`)
o la cabecera `X-Nexus-Token` con el token interno (`data/interno/token`; canales, healthcheck y
pruebas). Sin ella: 401 (las páginas redirigen a `/login`). Tras un intento fallido, 429 con
`Retry-After` y espera creciente. La contraseña se crea con `docker compose exec core python -m core.auth`.

## Web

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/` | Chat sobre las neuronas (la misma conversación que Telegram: `channel: "web"` se atiende como el chat del jefe) |
| POST | `/login` · `/logout` | Abre o cierra la sesión |
| GET | `/web/historial?n=40` | Últimos mensajes de la conversación |
| GET | `/web/panel` | Días de vida, elementos y gasto de hoy |
| GET | `/guardado` | Página «Lo guardado» |
| GET | `/guardado/lista?tipo=&q=&limit=&offset=` | Lo guardado (tipo: notas, documentos, fotos, audios, videos; q busca en nombre y contenido) |
| GET | `/guardado/{id}` | Detalle: texto, análisis (descripción, datos, notas), resumen y conceptos |
| GET | `/guardado/{id}/original` | El original, siempre como descarga (`attachment`, `octet-stream`, `nosniff`, CSP sandbox), por partes |
| GET | `/guardado/{id}/vista` | Vista previa de una foto, re-codificada como JPEG |

## Estado

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | `status` `ok` o `degradado` (si a algún trozo le falta vector con el modelo de embeddings actual); `embeddings`: modelo, dimensión, tabla, trozos y con vector; `llm_model`; `llm`: modelo, política de datos, presupuesto, último resultado, fallos seguidos y pausa. 503 si la base no responde |
| GET | `/profile` | Perfil activo: nombre del asistente, negocio, categorías, reglas proactivas, ejemplos |
| GET | `/worker` | Estado de los bucles, digestión en curso (`digiriendo`) y eventos por estado |
| GET | `/growth?days=14` | Días de vida, elementos aprendidos, pendientes y fallidos |
| GET | `/stats` | Elementos por tipo y pendientes |

## Enseñar (bandeja)

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/conversar` | `{"mensajes": ["..."], "channel": "telegram", "chat_id": "..."}`: un turno de conversación en lenguaje natural (varios mensajes seguidos van juntos). Devuelve `texto`, `botones` (filas de `{texto, dato}`), `accion`, `nivel` (`memoria`, `general` o null) y `llm` |
| POST | `/conversar/boton` | `{"dato": "rec:cancelar:12", "channel", "chat_id"}`: un botón pulsado |
| POST | `/conversar/archivo` | `{"filename", "kind", "duplicate", "chat_id"}`: acuse de recibo de un archivo ya subido |
| GET | `/charla?q=&desde=AAAA-MM-DD&hasta=AAAA-MM-DD` | Historial de la conversación por palabras y/o días |
| GET | `/charla/memoria` | Ficha del jefe y resumen corriente (separados del conocimiento con fuentes) |
| POST | `/inbox/text` | `{"text": "...", "source": "api"}` guarda una nota |
| POST | `/inbox/file?source=api` | Sube un archivo (multipart, campo `file`). Límite: `NEXUS_MAX_UPLOAD_MB` (413 si lo supera) |
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
| POST | `/ask` | `{"question": "..."}` responde con la memoria. Devuelve `answer`, `sources` (`n`, `cita`, `filename`, `snippet`, `distance`), `llm` (proveedor, modelo, tokens, `costo_usd`, `regenerada`) y `sin_verificar`: nombres o cifras de la respuesta que no están en las fuentes (también aparecen al final como «⚠ No pude verificar: …»). Si OpenRouter no puede responder (sin saldo, tope del día, caído), `tipo` es `aviso`, `answer` lo explica en una línea y `llm.aviso` trae la causa |
| GET | `/search?q=...&k=5` | Búsqueda híbrida sin redactar respuesta: trozos con `event_id`, `meta`, `distance` |
| POST | `/chat` | `{"message": "..."}` habla con el modelo **sin** memoria |
| GET | `/usage?days=30` | Uso del modelo de chat por día: peticiones, tokens, costo en USD (lo informa OpenRouter), `gastado_hoy_usd` y `presupuesto_diario_usd` |
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
| POST | `/reindex?todos=true` | Rehace todos desde los originales (tras cambiar el troceado) |
| POST | `/reindex?solo_vectores=true` | Tras cambiar de modelo de embeddings: respaldo y vectores de los trozos actuales en la tabla nueva, en segundo plano y retomable (sin re-trocear ni resumir) |
| GET | `/reindex` | Vectores de la tabla actual frente a los trozos, tabla vieja y progreso del último reindexado |
| POST | `/backup` | Respaldo inmediato y verificado |
| GET | `/backup` | Respaldos existentes y el último |

## Recordatorios y avisos

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/reminders/parse` | `{"text": "recuérdame mañana a las 9 llamar a Pedro"}` interpreta **sin guardar** (422 si no entiende la fecha o la hora es ambigua; `aviso` si la hora ya pasó hoy y se propone mañana) |
| POST | `/reminders/parse` con `channel` y `chat_id` | Además deja lo entendido pendiente de confirmar en la base (caduca) |
| GET | `/reminders/pending?channel=&chat_id=` | Lo que espera el "sí" en ese chat (`null` si nada); `reminder_id` lleno = ya confirmado |
| GET | `/reminders/pending/all` | Todas las confirmaciones guardadas |
| POST | `/reminders/confirm` | `{"channel", "chat_id"}` guarda lo pendiente una sola vez y devuelve la fila (`ya_guardado` si se repite); 404 si no hay nada |
| POST | `/reminders/pending/cancel` | `{"channel", "chat_id"}` descarta lo pendiente |
| POST | `/reminders/test?seconds=60` | `{"channel", "chat_id"}` recordatorio de prueba sin confirmación |
| POST | `/reminders` | `{"text", "due_at", "channel", "chat_id"}` guarda directamente |
| GET | `/reminders?chat_id=` | Abiertos y sin confirmar, con `estado` (creado, avisado_previo, avisado, esperando, pospuesto, sin_confirmar), `estado_es`, `proximo_local`, `insistencias` y `regla` |
| POST | `/reminders/done?chat_id=` | Marca hecho el último que sonó y espera respuesta |
| POST | `/reminders/{id}/done` · `/reminders/{id}/cancel?chat_id=` | Marca hecho o cancela (con `chat_id`, solo si es de ese chat) |
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
