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
FastAPI, SQLite (FTS5 + sqlite-vec), Ollama (qwen2.5:1.5b chat, nomic-embed-text embeddings),
faster-whisper (CPU), Docker Compose. Carpetas: core/, providers/, config/, senses/, tests/.

## Hecho (pasos 1 a 9)
1 Base del repo. 2 Docker (FastAPI + Ollama). 3 LLMProvider intercambiable + /chat.
4 Bandeja: originales en data/raw + tabla events. 5 Troceado, embeddings y búsqueda híbrida
(RRF) en /search. 6 /ask con fuentes y umbral NEXUS_MAX_DISTANCE=0.8. 7 Worker en segundo
plano (procesador + digestor, reintentos, /requeue, resúmenes y conceptos en tabla digests;
el resumen se indexa como chunk position=-1). 8 Audio con faster-whisper small en CPU.
9 /growth, /dashboard y respaldos verificados (backups/db + espejo de raw, retención 7).

## Pendiente
- Paso 10: desplegar en el servidor casero (Intel i5, 8 GB RAM, sin GPU, SSD 1 TB) y conectar
  la PC Windows con GTX 1650 (4 GB VRAM) cambiando solo OLLAMA_BASE_URL (red local o Tailscale,
  sin exponer puertos). Fijar versión de Ollama si sigue en :latest. Sincronizar respaldos con
  otro equipo. Pedir al usuario los datos del servidor y la IP de la PC con GPU.
- Después: canales (Telegram, voz, que NEXUS escriba o llame) y más sentidos (imágenes, actividad).
- Mejoras anotadas: /reindex, cola separada para audios largos, recalibrar NEXUS_MAX_DISTANCE
  con datos reales, timestamps en audio, regenerar resúmenes con un modelo mejor.
