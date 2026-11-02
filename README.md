# NEXUS

**Versión 0.1.0, uso personal.**

Asistente personal para el dueño de un negocio (primer caso: ventas). Le mandas
notas, archivos, fotos y notas de voz por Telegram o por la web; NEXUS guarda el original, lo
entiende y después responde tus preguntas **citando de dónde sale cada dato**. También te
escribe primero: recordatorios y un resumen cada mañana.

Empieza vacío y aprende día a día. La inteligencia vive en lo que acumula, no en el modelo: el
chat va por OpenRouter (el modelo se cambia con una variable) y la memoria, los embeddings, el
audio y el OCR corren en tu máquina, en Docker.

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
- **Respaldos** automáticos y **perfiles por rubro** sin tocar el código ([perfiles](docs/PERFILES.md)).
- **Chat por OpenRouter** con reintentos, costo por día (`/usage`), tope diario y solo proveedores
  sin retención ([chat](docs/MODO_NUBE.md)). Si falla o no hay saldo, nada se pierde: queda en cola.

### Límites conocidos

- **Preguntas que juntan varias notas** ("¿qué clientes pidieron laptops?"): acierta 2 de 4 en
  `tests/eval_ventas.py`. Las preguntas sobre una sola nota aciertan 9 de 9.
- **Sin internet o sin saldo en OpenRouter no conversa**: guarda todo y avisa en una línea.
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

Pon tu clave en `OPENROUTER_API_KEYS` dentro de `.env`. La primera vez descarga Ollama y el
modelo de embeddings (`nomic-embed-text`); el modelo de voz se descarga con el primer audio.

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
| `/estado` | Días de vida, elementos y pendientes |
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
| `OPENROUTER_API_KEYS` | vacío | Clave de OpenRouter |
| `NEXUS_OPENROUTER_MODEL` | `deepseek/deepseek-v4-flash` | Modelo de chat |
| `NEXUS_DAILY_BUDGET_USD` | vacío | Tope de gasto diario; al llegar avisa y espera a mañana (ver `GET /usage`) |
| `NEXUS_PROFILE` | `general` | Perfil de rubro (`profiles/*.toml`) |
| `NEXUS_TZ_OFFSET` | `-5` | Zona horaria para "hoy", recordatorios y resumen |
| `NEXUS_MAX_DISTANCE` / `NEXUS_FTS_MARGIN` | `0.78` / `0.04` | Qué tan parecido debe ser un recuerdo para usarlo |
| `NEXUS_OCR` / `NEXUS_VISION_MODEL` | `on` / vacío | OCR de imágenes; descripción de fotos opcional |
| `NEXUS_BACKUP_HOURS` / `NEXUS_BACKUP_HOST_DIR` | `24` / `./backups` | Respaldos automáticos y dónde guardarlos |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_IDS` | vacío | Bot de Telegram y quién puede usarlo |

Cambiar de modelo: edita `NEXUS_OPENROUTER_MODEL` y `docker compose up -d`.

## Privacidad

- Todo se guarda en tu máquina; no hay telemetría.
- Embeddings, audio, OCR y visión nunca salen de ella.
- `data/`, `backups/` y `.env` están en `.gitignore`. Tokens y keys solo viven en `.env`.
- Los puertos escuchan solo en `127.0.0.1`; para acceso remoto usa Tailscale o SSH.

> **Aviso.** Las preguntas y los textos que se resumen **se envían a OpenRouter** junto con los
> fragmentos de tu memoria que sirven para responder. NEXUS solo admite proveedores con
> retención cero que no guardan ni entrenan con tus datos ([detalles](docs/MODO_NUBE.md)).

## Pruebas

```bash
python3 tests/e2e.py                       # 18 casos en una instancia aislada temporal
python3 tests/e2e.py --chaos --audio voz.wav --audio-phrase "una palabra del audio"
python3 tests/eval_ventas.py --label prueba    # 21 notas y 17 preguntas de ventas (aislada)
python3 tests/informe.py                   # qué funciona hoy en TU instalación (instancia real)
python3 tests/calibrate.py                 # recalibra NEXUS_MAX_DISTANCE con tus datos (solo lee)
```

- **e2e**: levanta su propio contenedor con datos temporales; tu memoria real no se toca. Usa
  OpenRouter de verdad (centavos). Sin `--audio` o `--chaos`, esos casos quedan en SKIP.
  `--chaos` levanta otra instancia con una clave inválida: comprueba que el original se guarda,
  la digestión queda en cola con reintentos y el bot responde una línea clara.
- **eval_ventas**: mide recuperación, respuestas, "no sé" correctos, datos inventados y tiempos;
  guarda el resultado en `tests/eval_last.json` (ignorado por git) para comparar corridas.
- **informe**: corre contra la instancia en marcha. Guarda una nota de prueba y crea un
  recordatorio de prueba por el manejador real del bot, que luego cancela.
- Unitarias (dentro de la imagen):
  `docker run --rm -e NEXUS_DATA_DIR=/tmp -v "$PWD/tests:/app/tests:ro" nexus-core python -m unittest tests.test_reminders_flow`
  (el e2e las corre todas en el caso 12).

## Hoja de ruta

NEXUS pasará a llamarse **Isabelle**: una secretaria personal en Telegram, sin comandos.

1. Limpieza y chat solo por OpenRouter (hecho).
2. Isabelle: personalidad, conversación sin comandos, recordatorios con botones.
3. Ver y oír: comentar fotos, audios y documentos.
4. Embeddings sin Ollama.
5. Web: chat minimalista, subida de archivos, vista de lo guardado y contraseña.
6. Servidor: VPS (Ubuntu 24.04, 16 GB, 8 vCPU) solo por Tailscale y SSH con llave, con respaldos
   sincronizados a la laptop ([OPERACION](docs/OPERACION.md)).

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
| [MODO_NUBE](docs/MODO_NUBE.md) | Chat por OpenRouter: configurar, política de datos, fallos, costo |
| [CHANGELOG](CHANGELOG.md) | Cambios por versión |

## Estructura

```
core/       API, bandeja, worker, memoria, preguntas, recordatorios, interfaz web
senses/     un extractor por formato
providers/  chat (OpenRouter) y embeddings (Ollama local)
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
