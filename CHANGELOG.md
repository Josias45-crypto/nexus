# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Fechas en
AAAA-MM-DD.

## [Sin publicar]

### Cambiado
- El chat va solo por OpenRouter con un cliente único (`providers/openrouter.py`): reintentos
  1/2/4 s, avisos de una línea (402, 429, clave, caída), uso y costo en `/usage`, tope diario sin
  respaldo y política `zdr` + `data_collection=deny`. Ollama queda solo para embeddings.
- Si OpenRouter falla, la digestión queda en cola sin gastar intentos, con pausa creciente. Sin
  saldo (402), avisa por Telegram una sola vez.

### Agregado (Fase 2B: Isabelle conversa)
- Conversación en lenguaje natural, sin comandos (`POST /conversar`): reglas baratas primero y
  un intérprete con JSON validado solo si no alcanzan; acciones guardar, preguntar, analizar,
  opinar y charlar. Confirma solo al borrar o con una hora dudosa.
- Personalidad editable en `profiles/isabelle.toml`; nombre visible «Isabelle» en bot, web y textos.
- Memoria de la charla: historial con búsqueda (`GET /charla`), ficha del jefe y resumen
  (`GET /charla/memoria`), actualizados en segundo plano cuando nadie espera respuesta.
- Recordatorios con hora clara sin preguntar, con botones «Cambiar» y «Cancelar».
- Modelo potente por `NEXUS_OPENROUTER_MODEL_DEEP` («analiza a fondo», «piénsalo bien», «dame tu
  opinión»), avisando que cuesta más; `tests/comparar_modelos.py` para elegirlo.
- Canal: cola que junta mensajes seguidos, «escribiendo…», pausa humana y filtro de salida.

### Agregado (Fase 2C: recordatorios con seguimiento)
- Máquina de estados persistida (creado, avisado_previo, avisado, esperando, pospuesto, hecho,
  cancelado, sin_confirmar) con migración no destructiva; sin envíos duplicados ni IA en el
  programador; lo vencido sale con «(retrasado)».
- Aviso 30 min antes (si se creó con más anticipación), a la hora con «Ya lo hice» y
  «Recuérdame», 3 insistencias cada 20 min; repetición diaria, semanal y mensual.
- Lenguaje natural: «pospón el 2 una hora», «¿qué tengo pendiente?»; «en 90 segundos».
- `NEXUS_REMINDER_MAX_TRIES` se quita; `NEXUS_REMINDER_RETRY_MIN` queda solo para pruebas.

### Agregado (web por HTTP opcional)
- `NEXUS_BIND` (por defecto 127.0.0.1) en el puerto publicado; con 0.0.0.0 la web se sirve por HTTP.
- La cookie de sesión lleva `Secure` solo por HTTPS (`NEXUS_COOKIE_SECURE=auto|on|off`; `auto`
  mira el esquema o `X-Forwarded-Proto`), siempre con HttpOnly y SameSite=Strict.
- `/login` avisa «Conexión sin cifrar» por HTTP. Riesgos y vuelta al modo seguro en
  `docs/DESPLIEGUE.md` (paso 7b).

### Agregado (preparación del VPS compartido)
- `docker-compose.yml`: proyecto fijo `isabelle` sin `container_name` ni volúmenes con nombre;
  puerto del núcleo por `NEXUS_PORT` (por defecto 8000), siempre en 127.0.0.1; límites de memoria
  (núcleo 1 GB, canales 256 MB), `restart: unless-stopped`, healthcheck de los canales
  (`channels/salud.py`) y logs de Docker rotados (10 MB × 3).
- `docs/DESPLIEGUE.md` (usuario, Docker, clave de despliegue de solo lectura, copia del .env y los
  datos, `NEXUS_PORT=8100`, túnel SSH, actualizar, volver atrás) y `scripts/respaldo_remoto.sh`.
- Las pruebas y `tests/informe.py` encuentran los contenedores por `docker compose ps`.

### Corregido
- Isabelle llamó «señor Joaquín» al jefe. `nombre_jefe = "Josias"` en `[trato]` y regla de usar solo
  los tratamientos aprobados; `core/nombres.py` rechaza todo nombre de persona (tras un título, en
  vocativo o nombre de pila) que no salga de la ficha, las notas o la conversación: la charla y los
  saludos regeneran una vez y, si insiste, usan un respaldo fijo.

### Agregado (Fase 5: web)
- Chat transparente (solo letras y botones de texto) sobre una galaxia de neuronas en canvas con
  datos reales: notas, archivos y conceptos (máx. 300, los más recientes), conexiones por parecido
  y por conceptos compartidos, destellos al llegar algo, al buscar y al responder (`/brain/stream`).
  JS puro sin CDN, tope de 24/30 cuadros por segundo, pausa con la pestaña oculta y quieto con
  `prefers-reduced-motion`. Clip y arrastrar y soltar; panel esencial oculto (días, elementos,
  gasto de hoy). La web es la misma conversación de Telegram.
- «Lo guardado»: lista con filtros y búsqueda, detalle con análisis, vista previa re-codificada
  de las fotos y descarga del original (`attachment`, `nosniff`, CSP sandbox, por partes, ruta
  verificada dentro de `data/raw`).
- Seguridad: toda la API exige sesión (cookie HttpOnly, SameSite=Strict) salvo `/login`; canales,
  healthcheck y pruebas con token interno (`X-Nexus-Token`); contraseña scrypt creada en la
  terminal; espera creciente al fallar; cabeceras CSP, X-Frame-Options y nosniff; la contraseña
  nunca va a los registros. Tabla `sesiones` (solo la huella del token).
- `tests/muestra_web.py`: capturas en Chromium sin interfaz (Playwright en Docker).

### Quitado
- La imagen `ollama/ollama:0.35.1` (la etiqueta; el contenido sigue como `ollama:latest`).

### Cambiado (Fase 4: embeddings por OpenRouter, fuera Ollama)
- Embeddings por OpenRouter con zdr y `data_collection=deny`: `baai/bge-m3` (1024, 0,01 USD por
  millón de tokens), elegido entre 36 modelos por MRR 0,889 frente a 0,768 de nomic en las notas del
  eval; `NEXUS_EMBED_MODEL` y `NEXUS_EMBED_DIM` sin valor en el código. `embed()` en el cliente único
  (lotes de 64, reintentos 1/2/4 s, prefijos solo si el modelo los pide) y caché por huella
  (`embed_cache`). Uso aparte en `/usage` (`openrouter-embeddings`).
- Si OpenRouter falla: el original queda guardado, el procesamiento espera con pausa creciente
  sin gastar intentos y la búsqueda cae a solo palabras (FTS5) y lo dice.
- Tabla de vectores por modelo y dimensión (`vec_baai_bge_m3_1024`); `POST /reindex?solo_vectores=true`
  la llena desde los trozos actuales con respaldo y progreso (`GET /reindex`), sin tocar la vieja
  hasta comprobar. `/health` muestra los vectores en lugar de Ollama.
- Eval de ventas: recuperación 10/13 → 11/13, respuesta 12/13 → 13/13 (síntesis 3/4 → 4/4),
  «no sé» 4/4 y 0 datos inventados en ambos. Umbrales recalibrados: `NEXUS_MAX_DISTANCE` 0,92,
  `NEXUS_FTS_MARGIN` 0,05, `NEXUS_NOISE_DISTANCE` 1,08.
- Fuera Ollama: servicios `ollama` y `ollama-models`, `OLLAMA_BASE_URL`, su volumen y la imagen de
  9,4 GB; de 5 servicios a 3. La prueba de caos simula OpenRouter inalcanzable con un proxy.
- «a las 3 de la tarde», «9 de la noche», «7 de la mañana», «al mediodía» y «las 12 del mediodía»
  sin preguntar; «el viernes 13 a las 3» toma el 13 como día del mes (si no cae viernes, pregunta).
- Transcripción con el vocabulario del negocio (`[medios].vocabulario`): va como indicación y se
  corrige lo que suena igual («IGB» → «IGV»).

### Agregado (Fase 3: ver y oír)
- Fotos, PDF escaneados y audios por OpenRouter con zdr y `data_collection=deny`
  (`core/medios.py`): visión `NEXUS_OPENROUTER_MODEL_VISION` (qwen/qwen3.5-9b, ~0,0001 USD por
  foto) y transcripción `NEXUS_OPENROUTER_MODEL_AUDIO` (openai/whisper-large-v3-turbo, ~0,0002 USD
  por minuto) con el mismo cliente, reintentos, tope diario y registro en `/usage`
  (`openrouter-vision`, `openrouter-audio`).
- Procesar una sola vez: tabla `medios` ligada al original por su huella; lo repetido y lo
  reindexado no llaman a la IA; solo «vuelve a mirar la foto» lo analiza de nuevo.
- Isabelle comenta cada foto (1 a 3 frases, como mucho una pregunta, verificado por código) y pide
  confirmar las cifras leídas; lo ilegible lo dice; «se llama Max» queda ligado al medio; un álbum
  es una sola respuesta. PDF escaneados: hasta 20 páginas sin preguntar, luego avisa el costo.
- Notas de voz: se atienden como si él las hubiera escrito (recordatorios con las mismas reglas,
  sin duplicar la nota); filtro de alucinaciones de Whisper; «No lo escuché bien, ¿me lo repite?»
  si queda vacía o dudosa; tope de 15 min por audio en `[medios]` de `profiles/isabelle.toml`.
- Videos y videos redondos solo se guardan. Telegram de más de 20 MB: lo dice y sugiere la web.
- `POST /conversar/archivo` recibe una lista (álbum) con la leyenda; migración no destructiva
  (tabla `medios`). «Olvida eso» también borra el análisis.

### Quitado (Fase 3)
- Whisper local (faster-whisper), Tesseract OCR, `NEXUS_VISION_MODEL`, `NEXUS_WHISPER_*`,
  `NEXUS_OCR*` y el volumen `whisper_models`: la imagen `nexus-core` bajó de 1,03 GB a 563 MB.

### Agregado (Fase 2E: búsqueda en internet y «olvida eso»)
- Búsqueda en internet por OpenRouter (plugin `web`, buscador Exa, ~0,008 USD por búsqueda):
  primero el cerebro, luego el saber general avisado y solo después internet (dato reciente o
  local, el modelo no lo sabe, o «busca en internet…»); «no busques en internet» se respeta.
  Aviso aparte antes de buscar (`POST /conversar/seguir`), 1 a 3 fuentes legibles, verificación
  de nombres y cifras contra lo devuelto y «¿Quiere que lo guarde en su cerebro?» (nota
  `source=internet` con fuente y fecha; pendiente 30 min, sin tragarse mensajes).
- Privacidad: zdr no cubre al buscador; viaja solo la pregunta limpia (sin teléfonos, correos,
  montos, números largos ni nombres de su ficha o sus notas) con un sistema sin datos del jefe.
- Tope de 20 búsquedas al día en `[internet]` de `profiles/isabelle.toml` (tabla `busquedas`, sin
  la consulta); `/usage` separa el costo (`openrouter-internet`) y cuenta las búsquedas del día.
- «Olvida eso» en lenguaje natural con candidatos numerados (máx. 10), confirmación solo con «sí»
  y borrado en cascada: original y su copia del espejo de respaldos, trozos, FTS, vectores,
  resúmenes, ítems de la ficha (listados al confirmar), menciones en la charla, frases del
  resumen y recordatorios ligados. Tabla `olvidos` con tipo y fecha, sin contenido.
- Migraciones no destructivas (`busquedas`, `olvidos`). Al desplegar: `POST /backup` antes.

### Cambiado (ajuste 2D)
- El saludo pregunta en vez de suponer: la verificación rechaza «se ve que», «seguro que», «debe
  estar», «imagino que», «parece que», «se me hace que» con algo fuera del contexto, y no menciona
  la hora actual.

### Agregado (Fase 2D: iniciativa propia)
- Saludo de la mañana (7:30 a. m., con la agenda del día en 12 h y en orden) y de la noche
  (9:30 p. m., «¿cómo le fue?»), por el mismo programador y buzón que los recordatorios y dentro
  del historial de la charla. Horarios, silencio (11 p. m. a 6 a. m.), límites tras un apagón
  (11 a. m. / 11 p. m.) y 10 frases de respaldo por saludo en `[iniciativa]` de
  `profiles/isabelle.toml`.
- Una sola llamada al modelo de uso diario (temperatura 0,8) con ficha, resumen, agenda de hoy y
  mañana y lo real del día; el código verifica cifras, horas y nombres contra ese contexto, el
  largo, la «vida propia» y que no repita el arranque de los últimos 7 saludos.
- Sin hostigar: espera si el jefe está conversando (se salta tras 60 min); un día sin respuesta
  deja solo el de la mañana; a los 3 días pregunta «¿prefiere que le escriba menos?» y entiende
  «sí, menos», «no, como siempre» o «pausa»; una despedida de noche salta el saludo de la noche.
- Ajustes hablando: «mañana me levanto a las 5» (solo ese día, aunque caiga en el silencio) y
  «desde hoy el saludo a las 6» (persistente); «vuelve a saludarme», «escríbeme menos».
- Tabla `iniciativa` (migración no destructiva, `UNIQUE(dia, tipo)`: sin duplicados tras un
  reinicio) y `GET /iniciativa`. Al desplegar en la instancia real: `POST /backup` antes.

### Cambiado
- El programador ya no envía el resumen matutino de `core/briefing.py` (lo reemplaza el saludo
  de la mañana); `/briefing` y `/today` siguen disponibles.

### Ajustes a la Fase 2B
- Modelo potente `anthropic/claude-sonnet-5.5` con respaldo propio
  (`NEXUS_OPENROUTER_MODEL_DEEP_RESPALDO=deepseek/deepseek-v4-pro`) solo si está caído, limitado
  o no existe; nunca por saldo, clave o tope diario. Análisis a fondo en texto plano (~1500
  caracteres salvo que pida detalle), con supuestos y qué verificar.
- Personalidad: cariño medio, sección `[capacidades]` (lo que no puede ofrecer: imprimir,
  alarmas, correos, llamadas), 5 variantes de «no sale de mi memoria».
- Por código: tratamiento en la mitad de los mensajes como mucho (uno por mensaje, sin repetir
  el último) y emojis máx. 1 por mensaje y en 1 de cada 3 (`persona.dosificar`).
- [AHORA] en cada turno: fecha, día, hora en 12 h, momento del día y mañana.
- Fuentes legibles («De mi memoria: nota del 2 de nov sobre «Rosa Quispe…»») en vez de
  `Fuentes: nota.txt`; sin «Según la nota» y sin agregar detalles que la nota no dice.
- Recordatorios: hora sin am/pm se pregunta por texto («¿Las 7 de la mañana o de la noche?»),
  guardada en la base con caducidad de 30 min; si el siguiente mensaje no responde, se descarta.
  Una sola vez a menos de 2 h se toma; los repetidos siempre preguntan. Antes, con fecha, de 1 a
  7 era de la tarde.
- `tests/e2e.py` acepta `NEXUS_E2E_IMAGE` para probar una imagen construida aparte.

### Quitado
- Los comandos de Telegram (`/recordar`, `/hecho`, `/cancelar`...): todo es lenguaje natural.
- Modos `cloud`/`local`, backends por capacidad, respaldo local del chat, Groq y Gemini para chat,
  Ollama remoto, `/privado` y lo "privado solo local", el pie de proveedor y la racha.

## [0.1.0] - 2026-10-12

Primera versión: uso personal. Probada de extremo a extremo, con Telegram como canal
principal y documentada. Límites conocidos en el README.

### Recordatorios
- La confirmación ("¿Lo guardo?") se guarda en la base (`pending_confirmations`, caduca a los
  `NEXUS_REMINDER_CONFIRM_MIN` minutos) en vez de en la memoria del bot: sobrevive a reinicios.
  Antes, "Sí, guárdalo", "si por favor" o un reinicio perdían el recordatorio sin avisar.
- Acepta "sí, si, ok, dale, confirmo" y frases que empiezan así; si la respuesta no es clara o
  trae una pregunta, vuelve a preguntar. Solo dice "Guardado" con el n.º y la hora de Perú tras
  releer la fila; un "sí" repetido no la duplica (confirmación atómica).
- `/probar_recordatorio` (aviso a 60 s, cada etapa en el registro) y `/recordatorios` con hora de
  Perú y estado. e2e: caso 18, recordatorio que sobrevive al reinicio del contenedor.
- Horas: `4 : 17`, `4.17`, `4h17`, `4 y 18`, `y cuarto/media`, `menos cuarto`, `mañana 9`,
  medianoche. Sin am/pm ni fecha, la próxima ocurrencia entre h y h+12 (antes: de 1 a 7 siempre
  p. m. y se perdían los minutos: "hoy a las 4 : 17" a las 04:15 daba 16:00). Si la hora es
  ambigua o sobra un número, pregunta en vez de adivinar; con am/pm ya pasada, pregunta si es
  para mañana. Confirmación "hoy 4:17 a. m. (04:17, hora de Perú)" con la tarea aparte.
- `/cancelar N`, solo para recordatorios del mismo chat.

### Preguntas
- Las entidades de la pregunta (nombres propios, códigos, cifras; sin acentos ni mayúsculas,
  "3 200" = "3200") deben salir en alguna fuente; si no, "No tengo información". Ya no responde
  con el código de otro proyecto ni con datos de un cliente de nombre parecido.
- Segunda mirada si el modelo dice "no sé" habiendo fuentes que nombran lo preguntado; "qué hay
  anotado sobre X" va a la ruta de resumen. `NEXUS_LLM_TEMPERATURE` (0): respuestas
  reproducibles del modelo local.

### Informe
- `tests/informe.py`: revisa todas las tablas de recordatorios y el buzón, el estado de las
  confirmaciones pendientes y crea un recordatorio por el manejador real del bot (canal
  simulado, se cancela al final). Decir algo en vez de "no sé" ahora es FALLA.

### Repositorio
- `requirements.txt` declara `pydantic` y `numpy` (antes solo indirectas) con las versiones de
  `constraints.txt`; verificado con `pip install` y `pip check` en un contenedor limpio.
- `.gitignore` y `.dockerignore` cubren secretos (`.env*`), datos, respaldos, cachés y resultados
  de pruebas; README con estado real, límites conocidos y hoja de ruta; `.env.example` completo.

### Interfaz
- **Bandeja** en `/`: estado de cada elemento, motivo de error y botón **Reintentar**; aviso si
  algo lleva más de 10 min en cola.
- `/brain`: colores para imagen y video, motivo en el detalle y reintento con un clic.
- `GET /inbox` devuelve `procesando`, `reintentable` y el estado del resumen (`digest`).

### Sentidos (formatos)
- Registro de extractores en `senses/`: texto y código, CSV/TSV, JSON, XML, HTML, PDF (con OCR
  de páginas escaneadas), Word, Excel, PowerPoint, EPUB, correo `.eml` con adjuntos, `.zip`,
  imágenes con OCR (Tesseract), audio y video. Ver [docs/FORMATOS.md](docs/FORMATOS.md).
- Lo no legible queda `unsupported` con un motivo que dice qué hacer; el original se conserva.
- Imágenes y videos también se resumen (digestión).
- Descripción de fotos con un modelo de visión local, apagada por defecto.

### Conocimiento
- Troceado por estructura y origen en cada trozo; las citas las arma el código
  ("ventas.xlsx, hoja Ventas, fila 12", "contrato.pdf, pág. 3", "audio, min 01:23").
- Preguntas por fecha y "lo último" resueltas con la base, sin inventar.
- `POST /reindex` regenera trozos, vectores y resúmenes desde los originales.
- Umbral de relevancia recalibrado con datos reales (`NEXUS_MAX_DISTANCE=0.78`) y margen si
  también coincide por palabras (`NEXUS_FTS_MARGIN=0.04`).
- Digestión de documentos grandes con límite de secciones y progreso visible.

### Canales, perfiles y recordatorios
- Canal de **Telegram** por long polling (sin abrir puertos) y de **WhatsApp** oficial
  (apagado). Canal simulado para pruebas. Ver [docs/CANALES.md](docs/CANALES.md).
- Perfiles de rubro en `profiles/*.toml` (`general`, `ventas`).
- Recordatorios en español con confirmación antes de guardar, insistencia y `/hecho`.
- Resumen matutino proactivo con datos reales; buzón de salida (`outbox`).

### Modelos
- Modo nube de pruebas (Groq, Gemini, OpenRouter) con rotación de keys y respaldo local;
  **apagado** por defecto y nunca para lo privado. Ver [docs/MODO_NUBE.md](docs/MODO_NUBE.md).
- Ollama remoto opcional (`NEXUS_LLM_URL`) con respaldo en el local.

### Robustez
- SQLite en modo WAL; healthchecks; descarga automática de modelos; versiones fijadas.
- Bandeja con límite de tamaño y copia por bloques; cola de audio separada del texto.
- `/requeue` por estado o por elemento; errores en español; el token de Telegram nunca se
  registra.

### Pruebas y documentación
- `tests/e2e.py`: 18 casos sobre una instancia aislada (la memoria real no se toca), con
  caos (Ollama caído), audio, formatos, canal simulado y cascada de nube.
- Documentación: README, ARQUITECTURA, API, FORMATOS, OPERACION, MODO_NUBE, CANALES, PERFILES.

### Para actualizar desde el desarrollo previo
- Corre `POST /reindex?todos=true` una vez (cambió el troceado y se guardan metadatos de origen).
- `POST /requeue?estado=unsupported` para leer los formatos que antes no se entendían.

## Desarrollo previo (sin versión) - 2026-10-06

Pasos 1 a 9: base del repositorio, Docker con FastAPI y Ollama, proveedor de modelo
intercambiable, bandeja con originales, búsqueda híbrida (FTS5 + vectores), `/ask` con fuentes,
worker con reintentos, digestión, audio con faster-whisper, crecimiento, panel y respaldos
verificados.
