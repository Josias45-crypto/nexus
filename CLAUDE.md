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
deepseek/deepseek-v4-flash, solo proveedores zdr y data_collection=deny), Ollama 0.35.1 solo para
embeddings (nomic-embed-text), faster-whisper small (CPU), Tesseract OCR, Docker Compose
(profiles channels y whatsapp). Carpetas: core/, senses/, providers/, channels/, config/, profiles/, tests/, docs/.

## Visión: Isabelle
El proyecto y el bot se llamarán "Isabelle" (NEXUS e Isabelle son lo mismo). El nombre visible
(bot, web, textos) se cambia en la Fase 2; la carpeta, el repositorio y los contenedores se
renombran al final.
- Secretaria personal en Telegram, sin comandos. Trato: jefe, señor, señor Josias, gerente o
  ingeniero Josias. Sumisa pero siempre honesta, cariñosa y natural.
- Responde desde el cerebro; si no hay dato, avisa y busca en internet con OpenRouter.
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
Hasta su fase, audio, visión y embeddings siguen locales y sin interruptores.

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

## Cómo probar
`python3 tests/e2e.py --audio <wav> --audio-phrase <palabra> --chaos` (18 casos, instancia
aislada; la memoria real no se toca; usa OpenRouter real y --chaos simula OpenRouter inalcanzable). Unitarias dentro del contenedor (ver tests/test_senses.py).
Antes de probar cambios de código: `docker compose build core`. También: `tests/eval_ventas.py`
(medir antes y después en /ask) y `tests/informe.py` (estado de la instalación real).

## Reglas que no cambian
- Embeddings, audio, OCR y visión nunca salen de la máquina (hasta sus fases).
- Las citas las arma el código (core/citations.py), nunca el modelo.
- Nunca leer, imprimir ni registrar tokens o keys. No ejecutar /reindex sobre la base real sin OK.

## Pendiente
- Fases 2 a 6 de Isabelle (arriba). La 6 es desplegar en el VPS (Ubuntu 24.04, 16 GB, 8 vCPU,
  sin GPU) solo por Tailscale y SSH con llave; respaldos sincronizados a la laptop.
- Ideas: timestamps finos en audio, regenerar resúmenes con un mejor modelo, fechas de Excel.
