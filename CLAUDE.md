# NEXUS (Isabelle): contexto para Claude Code

## Esencia (no cambiar)
Cerebro personal que empieza vacío y aprende día a día. La inteligencia vive en el conocimiento
acumulado, no en el modelo. El chat va 100 % por API con OpenRouter (modelo por variable); la
memoria y los originales viven en la máquina.

## Principios
- Guardar siempre el original intacto; lo derivado (embeddings, resúmenes) debe poder regenerarse.
- Un módulo por sentido, todos conectados a la misma memoria.
- Datos y memoria en la máquina. Corre en Docker. Secretos solo en .env (nunca al repo).
- Código limpio: sin configuraciones "por si acaso" ni respaldos locales del chat.
- No cambiar la arquitectura sin preguntar.

## Cómo trabajar con el usuario (Josias)
- Un paso a la vez. Un commit por cada cambio que funciona (Conventional Commits).
- Respuestas breves. No releas el repo completo: lee solo los archivos necesarios.
- Antes de editar, resume el plan en máx. 5 líneas y espera el OK.
- No tocar data/, backups/ ni .env. Nunca `docker compose down -v`.

## Stack
FastAPI, SQLite (FTS5 + sqlite-vec, WAL), chat por OpenRouter (providers/openrouter.py, hoy
deepseek/deepseek-v4-flash; potente anthropic/claude-sonnet-5.5 con respaldo
deepseek/deepseek-v4-pro solo si está caído, 429 o no existe; solo proveedores zdr y
data_collection=deny; visión NEXUS_OPENROUTER_MODEL_VISION, hoy qwen/qwen3.5-9b; transcripción
NEXUS_OPENROUTER_MODEL_AUDIO, hoy openai/whisper-large-v3-turbo; embeddings NEXUS_EMBED_MODEL y
NEXUS_EMBED_DIM, hoy baai/bge-m3 de 1024), sin Ollama, Docker Compose
(profiles channels y whatsapp). Carpetas: core/, senses/, providers/, channels/, config/, profiles/, tests/, docs/.

## Visión: Isabelle
El proyecto y el bot se llamarán "Isabelle" (NEXUS e Isabelle son lo mismo). El nombre visible
(bot, web, textos) se cambia en la Fase 2; la carpeta, el repositorio y los contenedores se
renombran al final.
- Secretaria personal en Telegram, sin comandos. Trato: jefe, señor, señor Josias, gerente o
  ingeniero Josias. Sumisa pero siempre honesta, cariñosa y natural.
- Responde desde el cerebro; si no hay dato, avisa y busca en internet con OpenRouter (hecho en 2E).
- Comenta cada foto, audio o documento. Saludos de mañana y noche.
- Recordatorios con seguimiento y botones ("Ya lo hice", "Sí", "Recuérdame").
- Guarda lo importante de la charla; con "recuerda esto" (o equivalente) lo guarda tal cual.
- Web: chat transparente y minimalista sobre un fondo de neuronas vivas azul eléctrico, subida
  de archivos, vista para ver y descargar lo guardado, y contraseña.
- Los otros bots de Josias no interactúan con Isabelle.

## Fases
1. Limpieza y chat solo por OpenRouter (hecha).
2. Isabelle, reutilizando piezas de los otros bots: cola de mensajes, memoria por usuario,
   filtro de salida e iniciativa.
3. Ver y oír.
4. Embeddings sin Ollama (fuera Ollama).
5. Web.
6. Servidor.
7. Persona humana, recordatorios confiables y simulador definitivo (hecha: ver abajo).
8. Voz: notas de voz de Isabelle con texto a voz (pendiente).
Desde la Fase 4 todo modelo va por OpenRouter; en la máquina quedan la base, los originales y los respaldos.

## Propósito
Asistente PARA EL DUEÑO de un negocio (primer caso: ventas), usado sobre todo por Telegram. El
núcleo es genérico; lo propio del rubro vive en profiles/*.toml.

## Hecho
- Pasos 1 a 9 (base, Docker, LLM intercambiable, bandeja, búsqueda híbrida RRF, /ask con
  fuentes, worker, digestión, audio, crecimiento y respaldos).
- MISION.md fases 0 a 6 (v0.1.0, ver CHANGELOG.md): robustez, perfiles y recordatorios,
  canales (Telegram, WhatsApp apagado, simulado), modo nube de pruebas, conocimiento (troceado
  por estructura, citas por código, preguntas temporales, /reindex), sentidos por formato
  (senses/ con registro, OCR, video, zip) e interfaz con bandeja y reintento.
- Tras la fase 6 (en v0.1.0): confirmación de recordatorios persistente en la base
  (pending_confirmations), horas con minutos sin adivinar (core/when.py), /cancelar N por chat,
  filtro de entidades en /ask (core/verify.py), tests/informe.py.
- Fase 1 de Isabelle: chat solo por OpenRouter con reintentos, 402/429 claros, /usage, tope
  diario y aviso de 402 una vez; si falla, todo queda en cola. Sin modos, sin respaldo local,
  sin Groq/Gemini para chat, sin /privado ni racha.
- Fase 2B: conversación sin comandos (core/conversa.py: reglas baratas -> intérprete JSON
  validado -> acciones guardar/preguntar/analizar/opinar/charlar), personalidad en
  profiles/isabelle.toml (core/persona.py), memoria en tres capas (core/charla.py: historial con
  FTS, ficha del jefe solo de lo que él dice, resumen), filtro de salida (core/filtro.py), cola
  y botones en el canal, modelo potente por NEXUS_OPENROUTER_MODEL_DEEP, razonamiento apagado
  salvo en el modelo potente. Nombre visible: Isabelle.
- Fase 2C: recordatorios con máquina de estados en SQLite (core/reminders.py: previo 30 min,
  a la hora con «Ya lo hice»/«Recuérdame», 3 insistencias de 20 min, sin_confirmar), repetición
  diaria/semanal/mensual (core/when.py), botones del buzón, sin IA en el programador, textos con
  variantes en [recordatorios] de profiles/isabelle.toml.
- Ajustes 2B: cariño medio y [capacidades] en isabelle.toml; persona.dosificar() limita por
  código tratamientos (mitad de los mensajes, uno, sin repetir) y emojis (1, en 1 de cada 3);
  [AHORA] en cada turno; fuentes legibles (citations.legible); hora sin am/pm se pregunta por
  texto (pending_confirmations kind hora, 30 min; si no responde, se descarta), salvo una sola
  vez a menos de 2 h; los repetidos siempre preguntan.
- Fase 2D: iniciativa propia (core/iniciativa.py, tabla iniciativa con UNIQUE(dia, tipo)): saludo
  7:30 a. m. con agenda y 9:30 p. m., horarios en [iniciativa] de isabelle.toml; silencio 23-6
  (salvo hora pedida por él); espera si conversa (30/60 min); 1 día sin respuesta -> solo mañana;
  3 días -> «¿prefiere que le escriba menos?» (normal/menos/pausa en kv); despedida de noche salta
  la noche; apagado: hasta 11 a. m. / 11 p. m. Una llamada (modelo diario, 0,8) verificada por
  código (cifras, horas, nombres, arranque de los últimos 7); si no, respaldo. Reemplaza en el
  programador al resumen matutino de core/briefing.py (/briefing y /today siguen).
- Fase 2E: búsqueda en internet (core/internet.py; plugin web de OpenRouter con Exa: zdr no cubre al
  buscador, así que solo viaja la pregunta limpia, sin datos personales ni del cerebro). Niveles:
  cerebro -> saber general (NO_SE si no sabe) -> internet si es reciente/local, si el modelo no lo
  sabe o si lo pide; aviso aparte (Respuesta.seguir + POST /conversar/seguir); fuentes legibles
  verificadas; «¿lo guardo?» pendiente 30 min; tope [internet].busquedas_dia (tabla busquedas).
  «Olvida eso» (core/olvido.py): candidatos numerados, confirmación solo con «sí», cascada
  (original y su espejo en backups/raw, trozos, FTS, vectores, resúmenes, ficha, charla, resumen,
  recordatorios), tabla olvidos sin contenido. Preguntas pendientes en core/pendientes.py.
- Fase 3: ver y oír por OpenRouter con zdr (core/medios.py, tabla medios): cada foto, PDF escaneado
  o audio se analiza una sola vez (huella sha256) y lo repetido se reutiliza sin IA; solo «vuelve a
  mirar…» vuelve a llamarla. Fotos: JSON validado (datos solo si están en el texto) y comentario
  verificado (0,8; cifras y nombres del análisis, sin suponer) con «Leí …; ¿es correcto?»; la
  respuesta «se llama Max» queda ligada al medio. PDF escaneados: hasta [medios].pdf_paginas_max
  (20) y si hay más pregunta con el costo. Audios: tramos de 5 min hasta [medios].audio_max_min
  (15), filtro de alucinaciones, «no lo escuché bien» si queda dudoso; una nota de voz corta se
  atiende como si la hubiera escrito. Videos solo se guardan. Álbum: una respuesta. Telegram de más
  de 20 MB: lo dice. Sin Whisper local ni Tesseract (imagen de 1,03 GB a 563 MB).
- Fase 4: embeddings por OpenRouter (providers/openrouter.py embed(): lotes, reintentos, prefijos
  solo si el modelo los pide; caché por huella en embed_cache, providers/embeddings.py). Tabla de
  vectores por modelo y dimensión (db.VEC); POST /reindex?solo_vectores=true la llena con respaldo y
  progreso. Sin embeddings la búsqueda cae a FTS5 y lo dice (search.SOLO_TEXTO). Umbrales para
  bge-m3: 0,92 / 0,05 / ruido 1,08 (eval_ventas). Fuera Ollama (3 servicios). when.py entiende
  «de la tarde/noche/mañana» y «el viernes 13 a las 3»; vocabulario del negocio en la transcripción.
- Fase 5: web (core/ui.py, core/web.py, core/guardado.py, core/auth.py). Toda la API exige sesión
  (cookie HttpOnly + SameSite=Strict; contraseña scrypt en data/interno/web.json, creada con
  `docker compose exec core python -m core.auth`) salvo /login; canales y healthcheck con el token
  interno (X-Nexus-Token, data/interno/token, montado ro en telegram/whatsapp). Espera creciente al
  fallar. La web es la misma conversación de Telegram (web.conversacion). Neuronas en canvas con
  /brain/graph + /brain/stream (galaxia, tope de FPS, pausa oculta, reduced-motion). «Lo guardado»
  descarga siempre como attachment y verifica que la ruta esté en data/raw.
- Nombres: el jefe es Josias (trato.nombre_jefe). core/nombres.py: ningún nombre de persona fuera de
  ficha, notas o conversación en la charla (_hablar) ni en los saludos; regenera una vez y luego respaldo.
- VPS compartido (docs/DESPLIEGUE.md): compose «isabelle» sin nombres fijos, NEXUS_PORT (8100 en el VPS,
  siempre 127.0.0.1), límites 1 GB / 256 MB, logs 10 MB x 3, web por túnel SSH,
  scripts/respaldo_remoto.sh. En la laptop, la instalación previa es el proyecto «nexus»
  (docker compose -p nexus ...).
- Web por HTTP opcional: NEXUS_BIND=0.0.0.0 (por defecto 127.0.0.1); cookie Secure según
  NEXUS_COOKIE_SECURE (auto: esquema o X-Forwarded-Proto); /login avisa «Conexión sin cifrar».
- Persona humana (core/personaje.py): Isabelle actúa como persona; nunca menciona su naturaleza salvo
  pedido expreso («sal del personaje», «¿eres una IA?» en serio: responde con sinceridad y pregunta si
  vuelve). [biografia] en isabelle.toml con «### COMPLETAR ###» (no inventa lo vacío ni hechos físicos);
  errores en su voz y el detalle solo con «dime qué pasó» (OpenRouter.ultimo_fallo). Autorretrato:
  «esta eres tú» guarda su apariencia (kv personaje:apariencia) y entra al prompt; la web la muestra.
  La charla (conversa._sin_romper) regenera una vez y luego respaldo si inventa nombres, rompe el
  personaje, promete un aviso que no creó o supone cosas de él.
- Recordatorios confiables: «recuérdame» se detecta en cualquier parte del mensaje (antes, tras un
  saludo, la charla decía «queda anotado» sin crear nada); «no, era el jueves» corrige el último.
  Programador en su propio bucle (sin esperar a los saludos), despierta a la hora exacta, marca de
  vida y vigilante (reinicia el bucle; reenvía lo vencido > 2 min con «(retrasado)» y alerta);
  /health trae recordatorios_atrasados. Buzón: entregado solo con el acuse (message_id) y reintentos
  con espera creciente (POST /outbox/{id}/fallo, retry_after de Telegram); el canal revisa cada 5 s.
  scripts/diagnostico_remoto.sh (solo lectura) en el servidor.
- Simulador (tests/simulador/): jefe simulado por el camino del bot real, reloj simulado, 37
  escenarios, juez de IA; tests/tiempo_real.py prueba 42 min con el reloj real; informe en
  tests/informe_definitivo.md.

## Cómo probar
`python3 tests/e2e.py --audio <wav> --audio-phrase <palabra> --chaos` (24 casos, instancia
aislada; la memoria real no se toca; usa OpenRouter real y --chaos simula OpenRouter inalcanzable).
Para no tocar la imagen de los contenedores reales: `docker build -t nexus-core:prueba .` y
`NEXUS_E2E_IMAGE=nexus-core:prueba python3 tests/e2e.py --chaos`. Unitarias dentro del contenedor (ver tests/test_senses.py).
Antes de probar cambios de código: `docker compose build core`. También: `tests/eval_ventas.py`
(medir antes y después en /ask), `tests/informe.py` (estado de la instalación real),
`tests/muestra_conversaciones.py` (15 charlas para juzgar el tono), `tests/muestra_iniciativa.py` (una semana de saludos), `tests/muestra_busqueda_olvido.py`, `tests/muestra_ver_oir.py`, `tests/muestra_web.py` (capturas en Docker) y `tests/comparar_modelos.py`.

## Reglas que no cambian
- Los embeddings nunca salen de la máquina (hasta la Fase 4). Fotos, escaneos y audios solo a
  proveedores zdr + data_collection=deny; los videos no salen.
- Las citas las arma el código (core/citations.py), nunca el modelo.
- Nunca leer, imprimir ni registrar tokens o keys. No ejecutar /reindex sobre la base real sin OK.

## Pendiente
- Fases 2 a 6 de Isabelle (arriba). La 6 es desplegar en el VPS (Ubuntu 24.04, 16 GB, 8 vCPU,
  sin GPU) solo por Tailscale y SSH con llave; respaldos sincronizados a la laptop.
- Fase 8: notas de voz de Isabelle (texto a voz).
- La foto de perfil del bot de Telegram se cambia a mano en @BotFather (/setuserpic); el
  autorretrato de la conversación no la cambia.
- Ideas: timestamps finos en audio, regenerar resúmenes con un mejor modelo, fechas de Excel.
