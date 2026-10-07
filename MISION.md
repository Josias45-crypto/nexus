# MISIÓN v2: NEXUS terminado, probado y multicanal

Actúa como ingeniero senior y arquitecto. Piensa, razona y deduce: no ejecutes a ciegas.
Esencia (NO cambiar): cerebro personal que empieza vacío y aprende día a día; la inteligencia vive
en el conocimiento acumulado, no en el modelo; el original se guarda siempre intacto; un módulo por
sentido; el modelo es intercambiable por configuración.

## Privacidad (reemplaza cualquier instrucción anterior sobre Groq o nube)
- Por defecto TODO es local (Ollama, Whisper, embeddings).
- Existe un "modo nube de pruebas", opcional y apagado por defecto (NEXUS_ALLOW_CLOUD=off), solo
  para el chat y la digestión, con datos NO sensibles. Los embeddings y Whisper NUNCA salen de la máquina.
- Un documento puede marcarse como privado (private=1): si el contexto de una respuesta o digestión
  incluye un trozo privado, se usa SOLO el modelo local, aunque la nube esté activa.
- En producción con datos reales, NEXUS_ALLOW_CLOUD=off. Sin telemetría ni servicios externos
  salvo los que se activen a propósito y estén documentados.

## Restricciones
- Hardware objetivo: laptop o servidor de 8 GB de RAM, sin GPU. Docker Compose. Secretos solo en
  .env; NUNCA pidas, imprimas ni registres keys o tokens. Nunca toques data/, backups/ ni .env.
  Nunca `docker compose down -v`. Antes de cambiar el esquema, haz POST /backup y migra sin perder datos.
- Un commit por cambio que funciona (Conventional Commits). Si algo falla dos veces, para y
  explícame el dilema en vez de seguir parchando.

## Fase 0: auditoría (solo lectura; espera mi OK al final)
Lee CLAUDE.md, README.md, core/, providers/, config/, tests/ y docker-compose.yml. Ejecuta
`python3 tests/e2e.py` y revisa los logs. Entrégame en máx. 40 líneas: errores reales, riesgos
(concurrencia de SQLite, límites de tamaño, nombres de archivo, bloqueos del worker, recursos),
deuda técnica y qué falta frente al diseño. Conocido: el audio falla por la versión de av (puede
estar ya arreglado), hay eventos en failed, falta /reindex, cola separada para audios largos,
timestamps en audio, recalibrar NEXUS_MAX_DISTANCE, preguntas temporales ("qué aprendí hoy"),
citas fiables, digestión lenta de libros. Existe un proveedor de Groq: se rediseña en la Fase 4.

## Fase 1: robustez
SQLite con WAL y busy_timeout; healthchecks y depends_on con condición; límites de tamaño y
validaciones; errores claros en español; logging útil; apagado limpio; reintentos y /requeue para
todo; versiones fijas.

## Fase 2: archivos de todo tipo
Registro de extractores en senses/: extract(path, mime) devuelve segmentos con texto y metadatos
(página, hoja, marca de tiempo). Mínimo: txt, md, csv, json, xml, html, código, pdf (OCR local de
respaldo si es escaneado), docx, xlsx, pptx, epub, eml, imágenes (OCR local; descripción con
modelo de visión solo opcional y apagada por defecto), audio, video (extraer audio y transcribir)
y zip. Un formato desconocido no se pierde: se guarda el original, se marca "no soportado" con
motivo y se puede reprocesar. Documenta el tamaño final de la imagen.

## Fase 3: calidad del conocimiento
Troceado que respete la estructura. /reindex desde los originales. Digestión eficiente para
documentos grandes (por secciones, con progreso visible y límite configurable). Preguntas
temporales y meta ("qué aprendí hoy", "qué sabes de X", "resume lo último que subí"). Citas
armadas por el código. Recalibra el umbral con datos reales.

## Fase 4: modo nube de pruebas (rediseña lo de Groq)
- Pool de proveedores: Groq y Gemini (Google AI Studio), y OpenRouter opcional. Cada proveedor
  con lista de keys en .env (GROQ_API_KEYS=k1,k2,k3, GEMINI_API_KEYS=...). Modelos por variable
  de entorno, sin valores fijos en el código.
- Estrategia: orden configurable (NEXUS_CLOUD_ORDER=groq,gemini), rotación entre keys y ante
  429, 5xx o timeout enfriamiento de esa key (respeta Retry-After, mínimo 60 s), luego la
  siguiente key, luego el siguiente proveedor y, al final, Ollama local.
- Nunca registres keys ni el contenido de los prompts. /health y /worker muestran proveedor y
  modelo usados y el estado de cada key como "key#1" (sin exponer el valor).
- Respeta el flag private. La UI muestra una insignia roja "NUBE" cuando respondió la nube.
- Pruebas: keys falsas -> cae al local sin romperse; documento privado -> nunca va a la nube.
- docs/MODO_NUBE.md: cómo activarlo y apagarlo, y esta advertencia: abrir varias cuentas para
  multiplicar cuotas gratuitas puede violar los términos de los proveedores y arriesga el
  bloqueo; recomendar una key por proveedor.

## Fase 5: canales
- channels/ con una interfaz común (recibir mensaje, enviar, intención: recordar, preguntar,
  archivo, voz). Cada canal habla con la API de NEXUS.
- Telegram completo, como servicio aparte en Docker (profile "channels"), por long polling (sin
  abrir puertos), con httpx y sin dependencias pesadas. Token en TELEGRAM_BOT_TOKEN y lista de
  permitidos en TELEGRAM_ALLOWED_IDS; ignora a cualquier otro usuario. Comandos: /recordar,
  /pregunta (un texto libre sin comando también se trata como pregunta), /estado (growth),
  /hoy (qué aprendió hoy), /privado (marca el siguiente envío como privado). Notas de voz,
  archivos y fotos van a la bandeja. Mensajes proactivos: resumen matutino (hora configurable
  con NEXUS_TZ_OFFSET) y recordatorios simples ("recuérdame mañana a las 9 ...").
- WhatsApp: SOLO la vía oficial (WhatsApp Business Cloud API, webhook con verificación de firma),
  detrás de un interruptor apagado por defecto. No uses librerías no oficiales. Documenta lo que
  exige (cuenta de Meta Business, dirección pública con HTTPS).
- docs/CANALES.md con el aviso de privacidad: los bots de Telegram no cifran de extremo a extremo.
- Llamadas de voz: fuera de alcance; solo una nota de diseño.

## Fase 6: experiencia, pruebas y documentación
Interfaz (/) y cerebro (/brain) estables, con progreso, errores visibles y reintento desde la
interfaz. Amplía tests/e2e.py: un caso por tipo de archivo con fixtures sintéticos, preguntas
temporales, /reindex, caos, audio, cascada de proveedores y un canal simulado (sin llamar a
Telegram real). Documentación profesional en español: README.md, docs/ARQUITECTURA.md (con
diagrama), docs/API.md, docs/FORMATOS.md, docs/OPERACION.md, docs/MODO_NUBE.md, docs/CANALES.md,
CHANGELOG.md y CLAUDE.md actualizado. Prueba final: clonar en /tmp, `cp .env.example .env`,
`docker compose up -d --build` y pasar el e2e completo.

## Definición de terminado
e2e en verde; clon limpio funcionando; docs completas; imagen y RAM documentadas; todo
commiteado. Al terminar cada fase: e2e, commit y resumen en máx. 8 líneas de qué cambió y qué
decidiste. Usa /compact entre fases. Tras la Fase 4 dime qué variables debo poner en .env, y
tras la Fase 5 los pasos exactos para crear el bot.

---
# ADENDA: propósito real y orden revisado

Propósito: NEXUS es un asistente PARA EL DUEÑO de un negocio (primer caso: ventas), no un bot
que atiende a clientes finales. Se usa sobre todo por Telegram: el dueño manda información,
archivos y notas de voz, pregunta después y recibe datos y sugerencias; y NEXUS le escribe primero.
El núcleo debe seguir siendo genérico: lo específico de cada rubro vive en un perfil.

## Orden de ejecución (reemplaza el anterior)
Fase 0 -> Fase 1 -> Fase 5 y 5B -> Fase 4 -> Fase 3 -> Fase 2 -> Fase 6.

## Fase 5B: perfiles y recordatorios
- Perfiles en profiles/*.toml (tomllib de la librería estándar, sin dependencias nuevas),
  seleccionados con NEXUS_PROFILE. Campos: nombre del asistente, descripción del negocio, idioma,
  tono, instrucciones de sistema (persona), categorías de entidades (por ejemplo clientes,
  productos, ventas, tareas), reglas proactivas (seguimiento si un cliente lleva N días sin
  contacto, hora del resumen matutino) y preguntas frecuentes de ejemplo. El núcleo NO contiene
  lógica de ventas: todo lo propio del rubro sale del perfil. Entrega profiles/general.toml
  (por defecto) y profiles/ventas.toml, y docs/PERFILES.md con cómo crear uno nuevo (clínica,
  taller, estudio contable...).
- Recordatorios: tabla reminders (id, due_at en UTC, text, status, channel, chat_id,
  source_event_id, intentos). Se crean desde lenguaje natural en español ("recuérdame llamar a
  Pedro el viernes a las 9") con un intérprete de fechas determinista (hoy, mañana, pasado
  mañana, día de la semana, "en N horas o días", hora HH o HH:MM) y el LLM solo como respaldo.
  SIEMPRE confirma al usuario la fecha entendida antes de guardar. Un programador en el worker
  envía el aviso por el canal; si no hay respuesta, insiste cada X minutos hasta N veces
  (configurable). Comandos /recordatorios y /hecho.
- Resumen matutino proactivo: armado con datos reales (recordatorios del día, clientes sin
  seguimiento, lo último que entró), redactado por el LLM, y cada dato debe provenir de lo
  recuperado, sin inventar.
- Fase 5C, SOLO si 5B demuestra valor: tabla entities (tipo, nombre, atributos en JSON,
  evento de origen) alimentada con extracción por LLM validada contra esquema, descartando
  salidas inválidas; consultas como "clientes sin contacto hace 15 días" resueltas con SQL.
  Márcalo experimental: con un modelo de 1.5B la extracción no es fiable.
- Llamadas telefónicas: fuera de alcance; documenta las alternativas con su costo y privacidad.
- Pruebas: cambiar de perfil cambia el comportamiento sin tocar el núcleo; un recordatorio se
  crea con confirmación, se envía por un canal simulado y se reintenta si no hay respuesta.
