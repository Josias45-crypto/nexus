# NEXUS

Asistente **privado y local** para el dueño de un negocio. Le mandas notas, archivos, fotos y
notas de voz (por Telegram o la web); NEXUS guarda el original, lo entiende y después responde
tus preguntas **citando de dónde sale cada dato**. También te escribe primero: recordatorios y
un resumen cada mañana.

Empieza vacío y aprende día a día. La inteligencia vive en lo que acumula, no en el modelo: el
modelo es intercambiable por configuración y todo corre en tu máquina, en Docker.

## Qué hace

- **Entiende** texto, PDF (también escaneados), Word, Excel, PowerPoint, correos, zip, fotos
  (OCR), audio y video ([formatos](docs/FORMATOS.md)).
- **Responde con fuentes**: "Rosa debe 120 soles [1]" → `[1] ventas.xlsx, hoja Ventas, fila 12`.
  Si no lo sabe, lo dice.
- **Recuerda por fecha**: "¿qué aprendí ayer?", "resume lo último que te mandé".
- **Recordatorios** en español ("recuérdame el viernes a las 9 llamar a Pedro"), con
  confirmación e insistencia hasta que respondas `/hecho`.
- **Resumen matutino** con tus pendientes, clientes sin seguimiento y lo último que entró.
- **Perfiles por rubro** (ventas, general, o el tuyo) sin tocar el código ([perfiles](docs/PERFILES.md)).

## Inicio rápido

Requisitos: Docker con Compose v2, 8 GB de RAM y ~15 GB de disco libre ([recursos medidos](docs/OPERACION.md#recursos-medidos)).

```bash
git clone https://github.com/Josias45-crypto/nexus.git
cd nexus
cp .env.example .env
docker compose up -d --build
curl localhost:8000/health        # "status": "ok"
```

La primera vez descarga la imagen de Ollama y los modelos (`qwen2.5:1.5b` y `nomic-embed-text`)
de forma automática; el modelo de voz se descarga con el primer audio. Después funciona sin
internet.

Abre <http://localhost:8000>: escribe y pulsa **Recordar** para enseñarle, o **Preguntar**.
Arrastra archivos a la página. El botón **Bandeja** muestra el estado de cada uno.

### Telegram

1. Crea un bot con @BotFather y pon el token en `.env` (`TELEGRAM_BOT_TOKEN`).
2. Pon tu id numérico en `TELEGRAM_ALLOWED_IDS` (te lo da @userinfobot).
3. `docker compose --profile channels up -d --build`

Detalles, comandos y WhatsApp: [docs/CANALES.md](docs/CANALES.md).

## Páginas

| Ruta | Qué es |
|---|---|
| `/` | Conversación, subir archivos, grabar voz y Bandeja (estado, motivo, Reintentar) |
| `/brain` | El cerebro en vivo: recuerdos y conceptos como grafo |
| `/dashboard` | Crecimiento y respaldos |
| `/docs` | API interactiva ([referencia](docs/API.md)) |

## Configuración

Todo se ajusta en `.env`; cada variable está comentada en [.env.example](.env.example). Las más
usadas:

| Variable | Por defecto | Para qué |
|---|---|---|
| `NEXUS_LLM_MODEL` | `qwen2.5:1.5b` | Modelo de chat (se descarga solo al arrancar) |
| `NEXUS_PROFILE` | `general` | Perfil de rubro (`profiles/*.toml`) |
| `NEXUS_TZ_OFFSET` | `-5` | Zona horaria para "hoy", recordatorios y resumen |
| `NEXUS_MAX_DISTANCE` / `NEXUS_FTS_MARGIN` | `0.78` / `0.04` | Qué tan parecido debe ser un recuerdo para usarlo |
| `NEXUS_LLM_URL` | vacío | Ollama remoto con GPU (respaldo automático en el local) |
| `NEXUS_ALLOW_CLOUD` | `off` | Nube **solo para pruebas** ([MODO_NUBE](docs/MODO_NUBE.md)) |
| `NEXUS_OCR` / `NEXUS_VISION_MODEL` | `on` / vacío | OCR de imágenes; descripción de fotos opcional |
| `NEXUS_BACKUP_HOURS` / `NEXUS_BACKUP_HOST_DIR` | `24` / `./backups` | Respaldos automáticos y dónde guardarlos |

Cambiar de modelo: edita `NEXUS_LLM_MODEL` y `docker compose up -d` (se descarga solo).

## Privacidad

- Todo se procesa y guarda en tu máquina; no hay telemetría.
- Embeddings, audio, OCR, visión, recordatorios y todo lo marcado como **privado** nunca salen
  de ella, aunque actives la nube de pruebas.
- `data/`, `backups/` y `.env` están en `.gitignore`. Los tokens solo viven en `.env`.
- Los puertos escuchan solo en `127.0.0.1`; para acceso remoto usa Tailscale o SSH.

## Documentación

| Documento | Contenido |
|---|---|
| [ARQUITECTURA](docs/ARQUITECTURA.md) | Diagrama, recorrido de un archivo y de una pregunta, datos |
| [API](docs/API.md) | Todas las rutas con ejemplos |
| [FORMATOS](docs/FORMATOS.md) | Qué lee, qué no, OCR, límites, cómo agregar un formato |
| [OPERACION](docs/OPERACION.md) | Recursos, actualizar, respaldar, restaurar, servidor, problemas |
| [CANALES](docs/CANALES.md) | Telegram, WhatsApp, comandos y llamadas |
| [PERFILES](docs/PERFILES.md) | Crear un perfil para otro rubro |
| [MODO_NUBE](docs/MODO_NUBE.md) | Nube de pruebas: activar, apagar, cascada |
| [CHANGELOG](CHANGELOG.md) | Cambios por versión |

## Pruebas

```bash
python3 tests/e2e.py                       # 17 casos en una instancia aislada temporal
python3 tests/e2e.py --chaos --audio voz.wav --audio-phrase "una palabra del audio"
```

El e2e levanta su propio contenedor con datos temporales: tu memoria real no se toca. Sin
`--audio` o `--chaos`, esos casos se marcan SKIP.

## Comandos útiles

```bash
docker compose ps                        # estado (healthy)
docker compose logs -f core              # registros en vivo
docker compose up -d --build             # reconstruir tras actualizar
curl -X POST localhost:8000/backup       # respaldo ahora
docker compose down                      # detener (conserva datos y modelos)
```

> Nunca uses `docker compose down -v`: borra los volúmenes con los modelos descargados.

## Estructura

```
core/       API, bandeja, worker, memoria, preguntas, interfaz web
senses/     un extractor por formato
providers/  modelos: Ollama, remoto, nube de pruebas, embeddings
channels/   Telegram, WhatsApp, simulado
profiles/   perfiles de rubro
tests/      e2e, unitarias y fixtures
docs/       documentación
```

## Contribuir

Commits con [Conventional Commits](https://www.conventionalcommits.org/es/), uno por cambio que
funciona. Antes de subir: `python3 tests/e2e.py` en verde.

## Licencia

Uso personal. Todos los derechos reservados.
