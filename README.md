# Isabelle (NEXUS)

**Versión 0.1.0, uso personal.** NEXUS e Isabelle son lo mismo: el nombre visible ya es Isabelle;
la carpeta, el repositorio y los contenedores se renombran al final del proyecto.

Secretaria personal del dueño de un negocio (primer caso: ventas). Le escribes por Telegram como
a una persona, **sin comandos**, y le mandas notas, archivos, fotos y notas de voz; Isabelle
guarda el original, lo entiende, conversa con su personalidad (`profiles/isabelle.toml`) y
responde tus preguntas **citando de dónde sale cada dato**. También te escribe primero:
recordatorios y saludos de mañana (con tu agenda) y de noche, sin hostigar.

Empieza vacío y aprende día a día. La inteligencia vive en lo que acumula, no en el modelo: el
chat, la visión de fotos y la transcripción de audios van por OpenRouter (cada modelo se cambia
con una variable) y la memoria, los originales y los embeddings viven en tu máquina, en Docker.

## Estado real

### Qué funciona (probado de extremo a extremo)

- **Entiende** texto, PDF, Word, Excel, PowerPoint, EPUB, correos `.eml` y `.zip` sin IA
  ([formatos](docs/FORMATOS.md)).
- **Ve y oye** (Fase 3, por OpenRouter con zdr): comenta cada foto con naturalidad y como mucho una
  pregunta («¡qué ternura! ¿cómo se llama?»; «se llama Max» queda ligado a la foto); de boletas,
  facturas y capturas extrae los datos y pide confirmar las cifras («Leí S/ 450.00; ¿es
  correcto?»); lee PDF escaneados (hasta 20 páginas sin preguntar; si hay más, avisa el costo);
  transcribe notas de voz y las atiende como si las hubieras escrito; un álbum es una sola
  respuesta. Cada archivo se analiza **una sola vez** (huella sha256): si llega repetido lo dice
  sin volver a llamar a la IA, y solo lo vuelve a mirar si se lo pides. Los videos solo se guardan.
- **Responde con fuentes**: "Rosa debe 120 soles [1]" → `[1] ventas.xlsx, hoja Ventas, fila 12`.
  Las citas las arma el código, no el modelo. Si no lo sabe, dice "No tengo información sobre
  eso en mi memoria."; si la pregunta nombra algo (un cliente, un código, una cifra) que no está
  en ninguna nota, no responde con una nota parecida.
- **Recuerda por fecha**: "¿qué aprendí ayer?", "resume lo último que te mandé".
- **Recordatorios** en español ("recuérdame hoy a las 4:17 llamar a Piter", "todos los lunes a las
  8 am …"): con hora clara los guarda sin preguntar y muestra **Cambiar** y **Cancelar**; sin
  am/pm pregunta por texto «¿de la mañana o de la noche?» (salvo una sola vez a menos de 2 h). Avisa 30 min antes y a la hora con **Ya lo hice** y
  **Recuérdame**, insiste cada 20 min (máx. 3) y luego lo deja «sin confirmar». Sin IA, a prueba
  de reinicios y sin avisos duplicados ([detalles](docs/CANALES.md)).
- **Saludos por iniciativa propia** a las 7:30 a. m. (con la agenda del día en 12 h) y 9:30 p. m.
  («¿cómo le fue?»), redactados por la IA con lo real de tu día y verificados por código (si no
  pasan, frase de respaldo). Silencio de 11 p. m. a 6 a. m.; si estás conversando, espera; si no
  respondes un día, solo el de la mañana; a los 3 días pregunta «¿prefiere que le escriba menos?».
  Se ajusta hablando: «mañana me levanto a las 5», «desde hoy el saludo a las 6». Horarios en
  `[iniciativa]` de `profiles/isabelle.toml`; estado en `GET /iniciativa`.
- **Busca en internet** solo si no está en tu memoria y es reciente o local, si el modelo no lo
  sabe o si se lo pides («busca en internet…»); «no busques en internet» se respeta. Avisa antes en
  un mensaje aparte, cita 1 a 3 fuentes (sitio y dominio), marca lo que no vio en ellas y pregunta
  si lo guarda (como nota «de internet» con fuente y fecha). Tope de 20 búsquedas al día.
- **«Olvida eso»** («borra lo de Rosa», «olvida todo lo de X»): lista candidatos si hay varios y
  siempre confirma por texto («sí» y nada más). Borra el original, lo derivado, lo de tu ficha,
  las menciones en la charla y cancela los recordatorios ligados; queda un registro sin contenido.
- **Memoria de la charla**: historial completo con búsqueda, una ficha del jefe que sale solo de
  lo que él dice y un resumen corriente; va aparte del conocimiento con fuentes.
- **Telegram** como canal principal; interfaz web en `http://localhost:8000`.
- **Respaldos** automáticos y **perfiles por rubro** sin tocar el código ([perfiles](docs/PERFILES.md)).
- **Chat por OpenRouter** con reintentos, costo por día (`/usage`), tope diario y solo proveedores
  sin retención ([chat](docs/MODO_NUBE.md)). Si falla o no hay saldo, nada se pierde: queda en cola.

### Límites conocidos

- **Preguntas que juntan varias notas** ("¿qué clientes pidieron laptops?"): acierta 2 de 4 en
  `tests/eval_ventas.py`. Las preguntas sobre una sola nota aciertan 9 de 9.
- **Sin internet o sin saldo en OpenRouter no conversa**: guarda todo y avisa en una línea.
- **Sin verificar por el usuario en uso real**: la nota de voz por Telegram y los saludos de
  mañana y noche (pasan las pruebas automáticas, falta confirmarlos en el día a día).
- **Saludos**: la IA a veces supone lo que estás haciendo («se ve que arrancó temprano»); la
  verificación solo frena cifras, horas y nombres que no estén en el contexto.
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

Pon tu clave en `OPENROUTER_API_KEYS` dentro de `.env`. No descarga modelos: chat, visión,
audio y embeddings van por OpenRouter.

Crea la contraseña de la web (una vez; se guarda solo su hash):
`docker compose exec core python -m core.auth`. Abre <http://localhost:8000>: un chat
transparente sobre las neuronas vivas de tu memoria (notas, archivos y conceptos reales, con
destellos al aprender, buscar y responder). Es la misma conversación que Telegram. El clip 📎 o
arrastrar y soltar suben audio, fotos, videos y documentos (sin el tope de 20 MB de Telegram).
«lo guardado» lista todo, con su análisis y la descarga del original; «·» abre el panel
esencial (días de vida, elementos, gasto de hoy).

## Crear el bot de Telegram

1. En Telegram, abre **@BotFather**, envía `/newbot` y sigue los pasos (nombre y usuario que
   termine en `bot`). Te da un token.
2. Pon el token en `.env`: `TELEGRAM_BOT_TOKEN=...` (solo ahí; nunca lo compartas ni lo subas).
3. Averigua tu id numérico con **@userinfobot** y ponlo en `TELEGRAM_ALLOWED_IDS` (varios,
   separados por comas). El bot ignora a cualquier otra persona.
4. `docker compose --profile channels up -d --build` y escríbele «hola».

| Le escribes | Qué hace Isabelle |
|---|---|
| cualquier cosa, en lenguaje natural | Conversa con su personalidad (`profiles/isabelle.toml`); sin comandos |
| «recuerda esto: …» (o solo «recuerda esto») | Lo guarda tal cual (o guarda tu mensaje anterior) |
| un dato suelto («el local cierra a las 8») | Lo guarda y lo comenta; si ve un riesgo, te lo dice |
| una pregunta | Busca en su memoria y dice de dónde lo sacó («De mi memoria: nota del 2 de nov sobre «…»»); si no está, responde con lo que sabe en general y lo avisa |
| «recuérdame mañana a las 9 am …» | Lo guarda sin preguntar, dice lo que entendió (en 12 h, con a. m./p. m.) y muestra **Cambiar** y **Cancelar** |
| «recuérdame cada día a las 7 …» (sin am/pm) | Pregunta por texto «¿Las 7 de la mañana o de la noche, jefe?» y entiende «de la noche», «am», «7 pm» |
| «¿qué tengo pendiente?», «ya lo hice», «cancela el 3», «pospón el 2 una hora» | Lista con estado, marca hecho, cancela (pide confirmación) o pospone |
| «analiza a fondo…», «piénsalo bien…», «dame tu opinión…» | Usa el modelo potente (`NEXUS_OPENROUTER_MODEL_DEEP`) y avisa que cuesta más |
| «¿qué puedes hacer?» | La ayuda, en lenguaje natural |
| foto, álbum, PDF escaneado | Lo mira al llegar y lo comenta; cifras leídas: «¿es correcto?» |
| nota de voz | La transcribe y la atiende como si la hubiera escrito («recuérdame…» crea el recordatorio) |
| video, otros archivos | Se guarda el original; los videos no se analizan, los documentos se leen sin IA |
| «vuelve a mirar la foto» | Única forma de que vuelva a analizar un original (nueva llamada) |
| «ok ok», «gracias», «hasta mañana» | Cierra la charla sin otra pregunta |

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
| `NEXUS_OPENROUTER_MODEL_DEEP` | `anthropic/claude-sonnet-5.5` | Modelo potente para «analiza a fondo» |
| `NEXUS_OPENROUTER_MODEL_DEEP_RESPALDO` | `deepseek/deepseek-v4-pro` | Respaldo del potente si está caído, limitado o no existe (no por saldo ni tope) |
| `NEXUS_DAILY_BUDGET_USD` | vacío | Tope de gasto diario; al llegar avisa y espera a mañana (ver `GET /usage`) |
| `NEXUS_PROFILE` | `general` | Perfil de rubro (`profiles/*.toml`) |
| `NEXUS_TZ_OFFSET` | `-5` | Zona horaria para "hoy", recordatorios y resumen |
| `NEXUS_EMBED_MODEL` / `NEXUS_EMBED_DIM` | `baai/bge-m3` / `1024` | Embeddings por OpenRouter (0,01 USD por millón de tokens) |
| `NEXUS_MAX_DISTANCE` / `NEXUS_FTS_MARGIN` | `0.92` / `0.05` | Qué tan parecido debe ser un recuerdo para usarlo (calibrado para bge-m3) |
| `NEXUS_OPENROUTER_MODEL_VISION` | `qwen/qwen3.5-9b` | Visión: fotos y páginas escaneadas (~0,0001 USD por foto) |
| `NEXUS_OPENROUTER_MODEL_AUDIO` | `openai/whisper-large-v3-turbo` | Transcripción de audios (~0,0002 USD por minuto) |
| `NEXUS_BACKUP_HOURS` / `NEXUS_BACKUP_HOST_DIR` | `24` / `./backups` | Respaldos automáticos y dónde guardarlos |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_IDS` | vacío | Bot de Telegram y quién puede usarlo |

Cambiar de modelo: edita `NEXUS_OPENROUTER_MODEL` y `docker compose up -d`.

## Privacidad

- Todo se guarda en tu máquina; no hay telemetría.
- Los embeddings nunca salen de ella (hasta la Fase 4). Fotos, páginas escaneadas y audios viajan a
  OpenRouter solo a proveedores con retención cero (`zdr`) y `data_collection=deny`; el original
  queda siempre en tu máquina. Los videos no salen.
- `data/`, `backups/` y `.env` están en `.gitignore`. Tokens y keys solo viven en `.env`.
- Los puertos escuchan solo en `127.0.0.1`; para acceso remoto usa Tailscale o SSH.
- Toda la API exige sesión (cookie HttpOnly y SameSite=Strict, 30 días) salvo `/login`; los
  canales usan un token interno (`data/interno/token`, lo crea el núcleo). Tras un fallo la espera
  crece (1, 2, 4… s). Los originales se descargan siempre como archivo (nunca se ejecuta un HTML o
  SVG subido).

> **Aviso.** Las preguntas y los textos que se resumen **se envían a OpenRouter** junto con los
> fragmentos de tu memoria que sirven para responder. NEXUS solo admite proveedores con
> retención cero que no guardan ni entrenan con tus datos ([detalles](docs/MODO_NUBE.md)).

> **Búsqueda en internet.** La retención cero de OpenRouter cubre al modelo, **no al buscador**:
> la consulta llega a Exa, que declara no guardar consultas, pero eso no está garantizado para
> búsquedas que llegan por OpenRouter. Por eso solo viaja la pregunta reformulada, sin teléfonos,
> correos, montos ni nombres de tu ficha o de tus notas, y nunca fragmentos de tu memoria.

## Pruebas

```bash
python3 tests/e2e.py                       # 24 casos en una instancia aislada temporal
python3 tests/muestra_iniciativa.py        # una semana simulada de saludos (OpenRouter real)
python3 tests/muestra_busqueda_olvido.py   # 10 charlas de búsqueda y «olvida eso» (3 búsquedas reales)
python3 tests/muestra_ver_oir.py           # 10 casos de fotos, PDF, notas de voz y video (OpenRouter real)
python3 tests/muestra_web.py               # capturas de la web en un Chromium sin interfaz (Docker)
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
3. Ver y oír: comentar fotos, audios y documentos (hecho).
4. Embeddings sin Ollama (hecho).
5. Web: chat minimalista, subida de archivos, vista de lo guardado y contraseña (hecho).
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
| [FORMATOS](docs/FORMATOS.md) | Qué lee, qué no, ver y oír, límites, cómo agregar un formato |
| [OPERACION](docs/OPERACION.md) | Recursos, actualizar, respaldar, restaurar, servidor, problemas |
| [CANALES](docs/CANALES.md) | Telegram, WhatsApp, comandos y horas que entiende |
| [PERFILES](docs/PERFILES.md) | Crear un perfil para otro rubro |
| [MODO_NUBE](docs/MODO_NUBE.md) | Chat por OpenRouter: configurar, política de datos, fallos, costo |
| [CHANGELOG](CHANGELOG.md) | Cambios por versión |

## Estructura

```
core/       API, bandeja, worker, memoria, preguntas, recordatorios, interfaz web
senses/     un extractor por formato
providers/  OpenRouter: chat, visión, audio y embeddings (con caché por huella)
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
