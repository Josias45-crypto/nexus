# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Fechas en
AAAA-MM-DD.

## [Sin publicar]

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

## [1.0.0] - 2026-10-07

Primera versión completa: probada de extremo a extremo, multicanal y documentada.

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
- `tests/e2e.py`: 17 casos sobre una instancia aislada (la memoria real no se toca), con
  caos (Ollama caído), audio, formatos, canal simulado y cascada de nube.
- Documentación: README, ARQUITECTURA, API, FORMATOS, OPERACION, MODO_NUBE, CANALES, PERFILES.

### Para actualizar desde una versión anterior
- Corre `POST /reindex?todos=true` una vez (cambió el troceado y se guardan metadatos de origen).
- `POST /requeue?estado=unsupported` para leer los formatos que antes no se entendían.

## [0.9.0] - 2026-10-06

Pasos 1 a 9: base del repositorio, Docker con FastAPI y Ollama, proveedor de modelo
intercambiable, bandeja con originales, búsqueda híbrida (FTS5 + vectores), `/ask` con fuentes,
worker con reintentos, digestión, audio con faster-whisper, crecimiento, panel y respaldos
verificados.
