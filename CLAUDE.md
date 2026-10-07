# NEXUS: contexto para Claude Code

## Esencia (no cambiar)
Cerebro personal 100% local que empieza vacío y aprende día a día. La inteligencia vive en el
conocimiento acumulado, no en el modelo. El modelo es intercambiable por configuración.

## Principios
- Guardar siempre el original intacto; lo derivado (embeddings, resúmenes) debe poder regenerarse.
- Un módulo por sentido, todos conectados a la misma memoria.
- Todo local y privado. Corre en Docker. Secretos solo en .env (nunca al repo).
- No cambiar la arquitectura sin preguntar.

## Cómo trabajar con el usuario (Josias)
- Un paso a la vez. Un commit por cada cambio que funciona (Conventional Commits).
- Respuestas breves. No releas el repo completo: lee solo los archivos necesarios.
- Antes de editar, resume el plan en máx. 5 líneas y espera el OK.
- No tocar data/, backups/ ni .env. Nunca `docker compose down -v`.

## Stack
FastAPI, SQLite (FTS5 + sqlite-vec, WAL), Ollama 0.35.1 (qwen2.5:1.5b chat, nomic-embed-text
embeddings), faster-whisper small (CPU), Tesseract OCR, Docker Compose (profiles channels y
whatsapp). Carpetas: core/, senses/, providers/, channels/, config/, profiles/, tests/, docs/.

## Propósito
Asistente PARA EL DUEÑO de un negocio (primer caso: ventas), usado sobre todo por Telegram. El
núcleo es genérico; lo propio del rubro vive en profiles/*.toml.

## Hecho
- Pasos 1 a 9 (base, Docker, LLM intercambiable, bandeja, búsqueda híbrida RRF, /ask con
  fuentes, worker, digestión, audio, crecimiento y respaldos).
- MISION.md fases 0 a 6 (v1.0.0, ver CHANGELOG.md): robustez, perfiles y recordatorios,
  canales (Telegram, WhatsApp apagado, simulado), modo nube de pruebas, conocimiento (troceado
  por estructura, citas por código, preguntas temporales, /reindex), sentidos por formato
  (senses/ con registro, OCR, video, zip) e interfaz con bandeja y reintento.

## Cómo probar
`python3 tests/e2e.py --audio <wav> --audio-phrase <palabra> --chaos` (17 casos, instancia
aislada; la memoria real no se toca). Unitarias dentro del contenedor (ver tests/test_senses.py).
Antes de probar cambios de código: `docker compose build core`.

## Reglas que no cambian
- Embeddings, audio, OCR, visión y lo privado nunca salen de la máquina. Nube apagada por defecto.
- Las citas las arma el código (core/citations.py), nunca el modelo.
- Nunca leer, imprimir ni registrar tokens o keys. No ejecutar /reindex sobre la base real sin OK.

## Pendiente
- Paso 10: desplegar en el VPS (Ubuntu 24.04, 16 GB, 8 vCPU, sin GPU) solo por Tailscale y SSH
  con llave; respaldos sincronizados a la laptop (docs/OPERACION.md).
- Fase 5C (entidades extraídas por LLM): solo si el usuario lo decide; experimental con 1.5B.
- Ideas: timestamps finos en audio, regenerar resúmenes con un modelo mejor, fechas de Excel.
