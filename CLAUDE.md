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
- Paso 10: probar en la laptop y desplegar en un VPS de pago (Ubuntu 24.04, 16 GB RAM, 8 vCPU,
  sin GPU), accesible solo por Tailscale y SSH con llave, sin exponer puertos. Todo corre en CPU
  en el VPS. Fijar versión de Ollama si sigue en :latest. Respaldos del VPS sincronizados a la
  laptop. NEXUS_LLM_URL (LLM remoto con respaldo local, ya implementado) queda como opción para
  apuntar a un Ollama con GPU si algún día hay uno; los embeddings siempre van al local.
- Después: canales (Telegram, voz, que NEXUS escriba o llame) y más sentidos (imágenes, actividad).
- Mejoras anotadas: /reindex, cola separada para audios largos, recalibrar NEXUS_MAX_DISTANCE
  con datos reales, timestamps en audio, regenerar resúmenes con un modelo mejor.
