# NEXUS

**Versión 0.1.0, uso personal.**

Asistente **privado y local** para el dueño de un negocio (primer caso: ventas). Le mandas
notas, archivos, fotos y notas de voz por Telegram o por la web; NEXUS guarda el original, lo
entiende y después responde tus preguntas **citando de dónde sale cada dato**. También te
escribe primero: recordatorios y un resumen cada mañana.

Empieza vacío y aprende día a día. La inteligencia vive en lo que acumula, no en el modelo: el
modelo es intercambiable por configuración y todo corre en tu máquina, en Docker.

## Estado real

### Qué funciona (probado de extremo a extremo)

- **Entiende** texto, PDF (también escaneados, con OCR), Word, Excel, PowerPoint, EPUB, correos
  `.eml`, `.zip`, fotos (OCR), audio y video ([formatos](docs/FORMATOS.md)).
- **Responde con fuentes**: "Rosa debe 120 soles [1]" → `[1] ventas.xlsx, hoja Ventas, fila 12`.
  Las citas las arma el código, no el modelo. Si no lo sabe, dice "No tengo información sobre
  eso en mi memoria."; si la pregunta nombra algo (un cliente, un código, una cifra) que no está
  en ninguna nota, no responde con una nota parecida.
- **Recuerda por fecha**: "¿qué aprendí ayer?", "resume lo último que te mandé".
- **Recordatorios** en español ("recuérdame hoy a las 4:17 llamar a Piter"): siempre pide
  confirmación, sobrevive a reinicios del bot, pregunta si la hora es ambigua e insiste hasta
  que respondas `/hecho`.
- **Telegram** como canal principal; interfaz web en `http://localhost:8000`.
- **Respaldos** automáticos, **perfiles por rubro** sin tocar el código ([perfiles](docs/PERFILES.md))
  y un **modo nube de pruebas** apagado por defecto.

### Límites conocidos

- **Preguntas que juntan varias notas** ("¿qué clientes pidieron laptops?"): con el modelo local
  `qwen2.5:1.5b` acierta entre 0 y 2 de 4 en `tests/eval_ventas.py`; a veces omite o mezcla
  clientes. Las preguntas sobre una sola nota aciertan 9 de 9.
- **Gemini sin configurar**: la cascada de nube lo admite, pero no hay una key válida probada.
- **Groq gratis tiene límite**: tras unas 11 preguntas seguidas responde 429 y NEXUS cae al
  modelo local (más lento, de 2 a 10 s por respuesta en CPU).
- **Sin verificar por el usuario en uso real**: la nota de voz por Telegram y el resumen
  matutino (pasan las pruebas automáticas, falta confirmarlos en el día a día).
- **WhatsApp** está implementado pero apagado: exige una cuenta de Meta Business.
- No puede llamarte por teléfono (Telegram no lo permite).

## Inicio rápido

Requisitos: Docker con Compose v2, 8 GB de RAM y ~15 GB de disco libre
([recursos medidos](docs/OPERACION.md#recursos-medidos)).

```bash
git clone https://github.com/Josias45-crypto/nexus.git
cd nexus
cp .env.example .env
docker compose up -d --build
curl localhost:8000/health                 # "status": "ok"
docker compose --profile channels up -d    # bot de Telegram (ver abajo)
```

La primera vez descarga Ollama y los modelos (`qwen2.5:1.5b` y `nomic-embed-text`) de forma
automática; el modelo de voz se descarga con el primer audio. Después funciona sin internet.

Abre <http://localhost:8000>: escribe y pulsa **Recordar** para enseñarle, o **Preguntar**.
Arrastra archivos a la página. El botón **Bandeja** muestra el estado de cada uno.

## Crear el bot de Telegram

1. En Telegram, abre **@BotFather**, envía `/newbot` y sigue los pasos (nombre y usuario que
   termine en `bot`). Te da un token.
2. Pon el token en `.env`: `TELEGRAM_BOT_TOKEN=...` (solo ahí; nunca lo compartas ni lo subas).
3. Averigua tu id numérico con **@userinfobot** y ponlo en `TELEGRAM_ALLOWED_IDS` (varios,
   separados por comas). El bot ignora a cualquier otra persona.
4. `docker compose --profile channels up -d --build` y escríbele `/start`.

| Mensaje | Qué hace |
|---|---|
| texto libre o `/pregunta …` | Pregunta a la memoria y cita las fuentes |
| `/recordar …` | Guarda una nota |
| archivo, foto, nota de voz | Va a la bandeja: se guarda el original y se procesa |
| `recuérdame mañana a las 9 …` | Interpreta la fecha y pide confirmación (sí/no) antes de guardar |
| `/recordatorios` | Pendientes con hora de Perú y estado |
| `/hecho [n]` | Marca hecho el recordatorio n (sin número: el último que te avisó) |
| `/cancelar n` | Cancela el recordatorio n (solo los de tu chat) |
| `/probar_recordatorio` | Aviso de prueba a los 60 s |
| `/hoy` | Lo que aprendió hoy y los recordatorios del día |
| `/estado` | Días de vida, racha, elementos y pendientes |
| `/privado [texto]` | Lo siguiente que envíes (o ese texto) nunca sale a la nube |
| `/ayuda` | Ayuda con ejemplos del perfil activo |

Más detalles (horas que entiende, WhatsApp): [docs/CANALES.md](docs/CANALES.md).

## Páginas

| Ruta | Qué es |
|---|---|
| `/` | Conversación, subir archivos, grabar voz y Bandeja (estado, motivo, Reintentar) |
| `/brain` | El cerebro en vivo: recuerdos y conceptos como grafo |
| `/dashboard` | Crecimiento y respaldos |
| `/docs` | API interactiva ([referencia](docs/API.md)) |

## Configuración

Todo se ajusta en `.env`; cada variable está comentada en [.env.example](.env.example) con su
valor por defecto. Las más usadas:

| Variable | Por defecto | Para qué |
|---|---|---|
| `NEXUS_LLM_MODEL` | `qwen2.5:1.5b` | Modelo de chat (se descarga solo al arrancar) |
| `NEXUS_LLM_TEMPERATURE` | `0` | 0 = respuestas reproducibles del modelo local |
| `NEXUS_PROFILE` | `general` | Perfil de rubro (`profiles/*.toml`) |
| `NEXUS_TZ_OFFSET` | `-5` | Zona horaria para "hoy", recordatorios y resumen |
| `NEXUS_MAX_DISTANCE` / `NEXUS_FTS_MARGIN` | `0.78` / `0.04` | Qué tan parecido debe ser un recuerdo para usarlo |
| `NEXUS_LLM_URL` | vacío | Ollama remoto con GPU (respaldo automático en el local) |
| `NEXUS_ALLOW_CLOUD` | `off` | Nube **solo para pruebas** ([MODO_NUBE](docs/MODO_NUBE.md)) |
| `NEXUS_OCR` / `NEXUS_VISION_MODEL` | `on` / vacío | OCR de imágenes; descripción de fotos opcional |
| `NEXUS_BACKUP_HOURS` / `NEXUS_BACKUP_HOST_DIR` | `24` / `./backups` | Respaldos automáticos y dónde guardarlos |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_IDS` | vacío | Bot de Telegram y quién puede usarlo |

Cambiar de modelo: edita `NEXUS_LLM_MODEL` y `docker compose up -d` (se descarga solo).

## Privacidad

- Todo se procesa y guarda en tu máquina; no hay telemetría.
- Embeddings, audio, OCR, visión, recordatorios y todo lo marcado como **privado** nunca salen
  de ella.
- `data/`, `backups/` y `.env` están en `.gitignore`. Tokens y keys solo viven en `.env`.
- Los puertos escuchan solo en `127.0.0.1`; para acceso remoto usa Tailscale o SSH.

> **Aviso sobre el modo nube.** Con `NEXUS_ALLOW_CLOUD=on`, las preguntas y los textos que se
> resumen (si no son privados) **se envían a servicios externos** (Groq, Gemini u OpenRouter)
> junto con los fragmentos de tu memoria que sirven para responder. Esos servicios tienen sus
> propias políticas de datos. Úsalo solo con datos de prueba; para tus datos reales, déjalo en
> `off` (el valor por defecto) o marca lo sensible con `/privado`.

## Pruebas

```bash
python3 tests/e2e.py                       # 18 casos en una instancia aislada temporal
python3 tests/e2e.py --chaos --audio voz.wav --audio-phrase "una palabra del audio"
python3 tests/eval_ventas.py --label prueba    # 21 notas y 17 preguntas de ventas (aislada)
python3 tests/informe.py                   # qué funciona hoy en TU instalación (instancia real)
python3 tests/calibrate.py                 # recalibra NEXUS_MAX_DISTANCE con tus datos (solo lee)
```

- **e2e**: levanta su propio contenedor con datos temporales; tu memoria real no se toca. Sin
  `--audio` o `--chaos`, esos casos quedan en SKIP. `--chaos` detiene Ollama unos segundos.
- **eval_ventas**: mide recuperación, respuestas, "no sé" correctos, datos inventados y tiempos;
  guarda el resultado en `tests/eval_last.json` (ignorado por git) para comparar corridas.
- **informe**: corre contra la instancia en marcha. Guarda una nota de prueba y crea un
  recordatorio de prueba por el manejador real del bot, que luego cancela.
- Unitarias (dentro de la imagen):
  `docker run --rm -e NEXUS_DATA_DIR=/tmp -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_reminders_flow`
  (el e2e las corre todas en el caso 12).

## Hoja de ruta

- **Próximo**: desplegar en un VPS (Ubuntu 24.04, 16 GB, 8 vCPU, sin GPU) accesible solo por
  Tailscale y SSH con llave, con respaldos sincronizados a la laptop ([OPERACION](docs/OPERACION.md)).
- **Opcional**: entidades extraídas por el modelo (clientes, productos, montos); experimental con
  un modelo de 1.5B.
- **Ideas**: marcas de tiempo finas en audio, regenerar resúmenes con un modelo mejor, fechas de
  Excel, mejorar las preguntas que juntan varias notas.

## Comandos útiles

```bash
docker compose ps                        # estado (healthy)
docker compose logs -f core              # registros en vivo
docker compose up -d --build             # reconstruir tras actualizar
curl -X POST localhost:8000/backup       # respaldo ahora
docker compose down                      # detener (conserva datos y modelos)
```

> Nunca uses `docker compose down -v`: borra los volúmenes con los modelos descargados.

## Documentación

| Documento | Contenido |
|---|---|
| [ARQUITECTURA](docs/ARQUITECTURA.md) | Diagrama, recorrido de un archivo y de una pregunta, datos |
| [API](docs/API.md) | Todas las rutas con ejemplos |
| [FORMATOS](docs/FORMATOS.md) | Qué lee, qué no, OCR, límites, cómo agregar un formato |
| [OPERACION](docs/OPERACION.md) | Recursos, actualizar, respaldar, restaurar, servidor, problemas |
| [CANALES](docs/CANALES.md) | Telegram, WhatsApp, comandos y horas que entiende |
| [PERFILES](docs/PERFILES.md) | Crear un perfil para otro rubro |
| [MODO_NUBE](docs/MODO_NUBE.md) | Nube de pruebas: activar, apagar, cascada |
| [CHANGELOG](CHANGELOG.md) | Cambios por versión |

## Estructura

```
core/       API, bandeja, worker, memoria, preguntas, recordatorios, interfaz web
senses/     un extractor por formato
providers/  modelos: Ollama, remoto, nube de pruebas, embeddings
channels/   Telegram, WhatsApp, simulado
profiles/   perfiles de rubro
tests/      e2e, evaluación, informe, unitarias y fixtures
docs/       documentación
```

## Contribuir

Commits con [Conventional Commits](https://www.conventionalcommits.org/es/), uno por cambio que
funciona. Antes de subir: `python3 tests/e2e.py` en verde.

## Licencia

Uso personal. Todos los derechos reservados.
