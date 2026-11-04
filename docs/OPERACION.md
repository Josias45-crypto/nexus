# Operación

Cómo instalar, actualizar, respaldar, restaurar y vigilar NEXUS.

## Recursos (medidos)

Medido el 2026-10-07 en una laptop Linux sin GPU, con `docker images`, `docker system df -v`
y `docker stats`.

| Componente | Disco | RAM |
|---|---|---|
| Imagen `nexus-core` (Python, PyAV, Pillow; sin Whisper ni Tesseract desde la Fase 3) | 563 MB (antes 1,03 GB) | no se volvió a medir: ya no carga Whisper ni OCR en memoria |
| Canal de Telegram (misma imagen que `core`) | — | ~50 MB |

Desde la Fase 4 no hay Ollama: se fueron la imagen `ollama/ollama:0.35.1` (9,4 GB) y el modelo
`nomic-embed-text` (274 MB). Servicios: de 5 a 3 (`core`, y `telegram` y `whatsapp` por perfil).

**Mínimo razonable:** 2 núcleos, 2 GB de RAM y 2 GB de disco libre más tus datos. El servidor
previsto (8 vCPU, 16 GB) sobra.

Tiempos (laptop): una pregunta tarda de 5 a 25 s; una foto, unos 10 s con su comentario; una nota
de voz corta, 3 a 5 s (por OpenRouter); la digestión de un documento largo, 1 a 2 min. El volumen
`whisper_models` de versiones anteriores ya no se usa (se puede borrar a mano con
`docker volume rm nexus_whisper_models`).

## Instalar

```bash
git clone https://github.com/Josias45-crypto/nexus.git && cd nexus
cp .env.example .env          # ajusta lo que necesites (ver la tabla del README)
docker compose up -d --build  # construye una imagen de ~560 MB; no descarga modelos
curl localhost:8000/health    # "status": "ok"
```

Todos los modelos van por OpenRouter: `up` solo construye la imagen. Si se corta internet,
repite el comando: continúa donde quedó.

Después, crea la contraseña de la web: `docker compose exec core python -m core.auth` (pide
la contraseña dos veces; sin terminal interactiva genera una al azar y la muestra una sola vez).
Cambiarla cierra todas las sesiones. Se guarda solo su hash en `data/interno/web.json`.

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
| Salud | `curl localhost:8000/health` (`degradado` = hay trozos sin vector en la tabla del modelo actual: `POST /reindex?solo_vectores=true`). `docker compose ps` muestra `unhealthy` si no responde |
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
| «No pude conectar con OpenRouter» o «sin saldo» | Revisa `GET /health` (`llm.ultimo`) y el saldo en OpenRouter; lo que llega queda guardado y se procesa solo |
| Un audio largo no avanza | Va en su propia cola; no frena al texto. Revisa `docker compose logs core` |
| Telegram no responde | Falta tu id en `TELEGRAM_ALLOWED_IDS`: búscalo en `docker logs nexus-telegram` |
