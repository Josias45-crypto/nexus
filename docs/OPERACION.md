# Operación

Cómo instalar, actualizar, respaldar, restaurar y vigilar NEXUS.

## Recursos (medidos)

Medido el 2026-10-07 en una laptop Linux sin GPU, con `docker images`, `docker system df -v`
y `docker stats`.

| Componente | Disco | RAM |
|---|---|---|
| Imagen `nexus-core` (Python, Tesseract, PyAV, faster-whisper) | 1,03 GB | 49 MB en reposo · 775 MB de pico (Whisper y OCR durante el e2e) con audio y OCR |
| Imagen `ollama/ollama:0.35.1` (trae librerías de GPU aunque no se usen) | 9,4 GB | — |
| Modelos de Ollama (`qwen2.5:1.5b` 986 MB + `nomic-embed-text` 274 MB) | 1,26 GB | 1,5 GB con ambos cargados · 1,6 GB de pico |
| Modelo de voz Whisper `small` (se descarga al primer audio) | 486 MB | incluido en `nexus-core` |
| Canal de Telegram (misma imagen que `core`) | — | ~50 MB |

**Mínimo razonable:** 4 núcleos, 8 GB de RAM y 15 GB de disco libre más tus datos. El servidor
previsto (8 vCPU, 16 GB) sobra; deja margen para un modelo de chat más grande.

Tiempos en CPU (laptop): una pregunta tarda de 5 a 25 s; un audio de 1 min, unos 20 s; una
página escaneada con OCR, unos segundos; la digestión de un documento largo, 1 a 2 min.

## Instalar

```bash
git clone https://github.com/Josias45-crypto/nexus.git && cd nexus
cp .env.example .env          # ajusta lo que necesites (ver la tabla del README)
docker compose up -d --build  # la primera vez descarga ~11 GB entre imagen y modelos
curl localhost:8000/health    # "status": "ok"
```

`ollama-models` descarga los modelos que falten y termina; `core` espera a que acabe. Si se
corta internet, repite el comando: continúa donde quedó.

## Actualizar

```bash
git pull
curl -X POST localhost:8000/backup             # respaldo antes de cambiar nada
docker compose up -d --build                   # reconstruye y reinicia (con canales: --profile channels)
python3 tests/e2e.py                           # opcional: prueba en una instancia aislada
```

Los cambios de esquema de la base se aplican solos al arrancar (`core/db.py`) sin borrar datos.
Si una versión cambia el troceado o el modelo de embeddings, el CHANGELOG lo dice: entonces
corre `curl -X POST "localhost:8000/reindex?todos=true"`.

## Respaldos

- Automáticos cada `NEXUS_BACKUP_HOURS` (24) y a pedido con `POST /backup`.
- Cada copia de la base se **verifica** (`quick_check`) antes de darla por buena; se guardan
  las últimas `NEXUS_BACKUP_KEEP` (7) en `backups/db/`.
- Los originales se copian de forma incremental a `backups/raw/` (nunca cambian).
- Lleva los respaldos fuera de la máquina: apunta `NEXUS_BACKUP_HOST_DIR` a un disco externo,
  o desde la laptop sincroniza el servidor:
  `rsync -a --delete usuario@nexus-vps:nexus/backups/ ~/nexus-backups/`.

### Restaurar

```bash
docker compose stop core telegram                                  # nada escribiendo
mv data/nexus.db data/nexus.db.antes                               # conserva la actual
rm -f data/nexus.db-wal data/nexus.db-shm
cp backups/db/nexus-AAAAMMDD-HHMMSS.db data/nexus.db
rsync -a backups/raw/ data/raw/                                    # solo agrega originales faltantes
docker compose up -d
curl localhost:8000/growth                                         # revisa el número de elementos
```

Si solo perdiste trozos o vectores (la base está bien), no restaures: `POST /reindex?todos=true`
los regenera desde los originales.

## Vigilar

| Qué | Cómo |
|---|---|
| Salud | `curl localhost:8000/health` (`degradado` = Ollama caído o falta un modelo). `docker compose ps` muestra `unhealthy` si no responde |
| Qué entró y qué falló | Botón **Bandeja** en `/`, o `curl 'localhost:8000/inbox?limit=50'` |
| Worker y digestión | `curl localhost:8000/worker` |
| Registros | `docker compose logs -f core` (`NEXUS_LOG_LEVEL=DEBUG` para más detalle) |
| Telegram | `docker logs -f nexus-telegram` (el token nunca aparece en los registros) |
| Crecimiento y respaldos | `/dashboard` |

Elementos con aviso: `failed` (se agotaron los intentos), `unsupported` (formato no legible,
con motivo) o `empty` (sin texto). Se reintentan con el botón **Reintentar** o con
`POST /requeue?estado=todos`.

## Servidor (VPS)

Plan: Ubuntu 24.04, 16 GB RAM, 8 vCPU, sin GPU, accesible **solo** por Tailscale y SSH con llave.

1. SSH solo con llave (`PasswordAuthentication no`) y firewall que niegue todo lo entrante salvo
   SSH: `ufw default deny incoming && ufw allow OpenSSH && ufw enable`.
2. Instala Docker y Tailscale (`tailscale up`). La API queda en `127.0.0.1:8000`; para verla
   desde la laptop: `ssh -L 8000:localhost:8000 usuario@nexus-vps` o `tailscale serve 8000`.
3. Instala NEXUS como en "Instalar" y, si usas Telegram, `docker compose --profile channels up -d`
   (long polling: no necesita abrir puertos).
4. Programa la sincronización de respaldos hacia la laptop (`rsync` de arriba, con cron).

No publiques el puerto 8000 en internet: la API no tiene usuarios ni contraseñas porque está
pensada para quedar detrás de Tailscale.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `curl` falla justo después de `up` | `core` tarda en arrancar; espera a `docker compose ps` → `healthy` |
| `env file .env not found` | Falta `cp .env.example .env` |
| Permisos en `data/` | El contenedor usa UID 1000: `sudo chown -R 1000:1000 data backups` |
| "No lo sé" con algo que subiste | Mira la Bandeja: puede seguir en cola o haber quedado `empty`/`unsupported` |
| Respuestas lentas | Normal en CPU. Modelo más chico o `NEXUS_LLM_URL` a un Ollama con GPU |
| Un audio largo no avanza | Va en su propia cola; no frena al texto. Revisa `docker compose logs core` |
| Telegram no responde | Falta tu id en `TELEGRAM_ALLOWED_IDS`: búscalo en `docker logs nexus-telegram` |
