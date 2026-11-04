# Formatos que entiende NEXUS

Todo lo que llega a la bandeja (web, `POST /inbox/file`, Telegram) se guarda **intacto** en
`data/raw`. Después, un *sentido* (`senses/`) saca el texto con su origen, lo trocea e indexa,
sin IA. Fotos, páginas escaneadas y audios los analiza `core/medios.py` **una sola vez** por
OpenRouter (solo proveedores `zdr` y `data_collection=deny`); los videos solo se guardan.

| Tipo | Extensiones | Qué se extrae | Origen en la cita |
|---|---|---|---|
| Texto | .txt .md .markdown .log .rst .org (y archivos sin extensión que sean texto) | Todo; en Markdown, los `#` marcan secciones | `§ Sección` |
| Código y configuración | .py .js .ts .java .go .rs .c .cpp .cs .rb .php .sh .sql .css .yaml .toml .ini … | Todo, sin tomar `# comentarios` como secciones | — |
| Tablas | .csv .tsv (detecta `,` `;` tab `\|`) | Cada fila como `columna: valor; …` | `fila 2–40` |
| JSON | .json .jsonl .ndjson .geojson | `ruta.de.la.clave: valor` | — |
| XML | .xml .svg .plist .gpx .kml | `etiqueta: texto` | — |
| Web | .html .htm .xhtml | Texto visible; los títulos `<h1>`… como secciones; sin scripts ni estilos | `§ Sección` |
| PDF | .pdf | Texto por página; **páginas escaneadas: modelo de visión** (hasta 20 sin preguntar) | `pág. 3–4` |
| Word | .docx .docm .dotx | Párrafos, títulos (como secciones), listas y tablas | `§ Sección` |
| Excel | .xlsx .xlsm | Cada hoja; cada fila como `encabezado: valor; …` | `hoja Ventas, fila 2–30` |
| PowerPoint | .pptx .pptm .ppsx | Texto de cada diapositiva en orden de presentación, más sus notas | `diap. 5` |
| Libros | .epub | Capítulos en orden de lectura | `cap. 3` |
| Correo | .eml | Asunto, remitente, destinatarios, fecha, cuerpo y **adjuntos legibles** | `adjunto.pdf, pág. 2` |
| Imágenes | .png .jpg .jpeg .webp .gif .bmp .tif .tiff | **Modelo de visión**: descripción, texto legible, datos (montos, fechas, nombres) y lo ilegible | — |
| Audio | .mp3 .wav .m4a .ogg .oga .opus .flac .aac .wma .amr | Transcripción por OpenRouter (hasta 15 min; el resto queda guardado) | `min 01:23` |
| Video | .mp4 .mkv .mov .webm .avi .m4v .3gp .mpeg .mpg .wmv | **Nada: solo se guarda** el original | — |
| Comprimidos | .zip | Cada archivo de adentro con su propio sentido (hasta 2 niveles) | `carpeta/nota.md, § Tema` |

Si la extensión no se reconoce, se usa el tipo MIME; si tampoco, y el contenido es texto
UTF-8, se lee como texto.

## Lo que no se lee (y queda guardado)

El elemento queda en estado `unsupported` con el motivo, y el original se conserva. Cuando
NEXUS aprenda ese formato, `POST /requeue?estado=unsupported` lo reprocesa.

| Caso | Qué hacer |
|---|---|
| Office antiguo (.doc .xls .ppt) | Guardarlo como .docx/.xlsx/.pptx o PDF |
| Outlook .msg | Guardarlo como .eml |
| .rar .7z | Comprimir como .zip |
| Fotos HEIC/HEIF (iPhone) | Enviarlas como JPG (Telegram ya las convierte al mandarlas como foto) |
| PDF o documento con contraseña, zip cifrado | Quitar la contraseña antes de subirlo |
| Cualquier otro binario | Queda guardado sin texto |

Dentro de un .zip o de un correo se omiten las fotos, los audios y los videos (súbelos aparte) y
lo que no se pueda leer; el resto sí se indexa.

Estados sin texto (`empty`): un video (solo se guarda) o un audio sin voz clara. El motivo
aparece en la web y en `GET /inbox`.

## Ver y oír (Fase 3, `core/medios.py`)

- **Una sola vez**: el análisis queda en la tabla `medios` ligado al original (la huella sha256 de
  `events`). Si el mismo archivo vuelve a llegar, o se reindexa, no se llama a la IA. Solo «vuelve a
  mirar la foto» (o «escucha otra vez el audio») lo analiza de nuevo.
- **Fotos** (`NEXUS_OPENROUTER_MODEL_VISION`, hoy `qwen/qwen3.5-9b`, ~0,0001 USD por foto): se
  achican a 1600 px y el modelo responde un JSON validado. Un dato solo se acepta si aparece en el
  texto leído; lo ilegible se dice. Los trozos: descripción, texto legible y «Datos leídos».
- **PDF escaneados**: las páginas sin texto (menos de 25 caracteres) se envían como imagen a
  150 ppp, hasta `[medios].pdf_paginas_max` (20); si hay más, Isabelle avisa el costo aproximado
  y pregunta. Trozos con `"vision": true` y su página.
- **Audios** (`NEXUS_OPENROUTER_MODEL_AUDIO`, hoy `openai/whisper-large-v3-turbo`, ~0,0002 USD por
  minuto): se pasan a Opus mono de 16 kHz y viajan en tramos de 5 min, hasta
  `[medios].audio_max_min` (15). Se descartan las frases que Whisper inventa en silencio
  («Subtítulos realizados por…», «¡Gracias!», bucles y segmentos de baja confianza); si no queda
  nada claro, Isabelle pide que se lo repitan y no entra nada a la memoria.
- **Videos**: solo se guardan; no salen de la máquina.
- La imagen `nexus-core` bajó de 1,03 GB a 563 MB al quitar Whisper local y Tesseract.

## Límites

| Variable | Por defecto | Qué limita |
|---|---|---|
| `NEXUS_MAX_UPLOAD_MB` | 50 | Tamaño de cada archivo recibido (Telegram: 20 MB) |
| `NEXUS_MAX_TEXT_CHARS` | 2 000 000 | Texto indexado por elemento; el resto se ignora (se registra un aviso) |
| `[medios].pdf_paginas_max` | 20 | Páginas escaneadas leídas sin preguntar (`profiles/isabelle.toml`) |
| `[medios].audio_max_min` | 15 | Minutos transcritos por audio; el resto queda guardado sin transcribir |
| `NEXUS_ARCHIVE_MAX_FILES` | 200 | Archivos leídos de un .zip |
| `NEXUS_ARCHIVE_MAX_MB` | 200 | MB descomprimidos leídos de un .zip (protege de "zips bomba") |

Las fechas de Excel aparecen como número de serie (p. ej. `45930`): el archivo no guarda el
formato de fecha en la celda, sino en el estilo, que NEXUS no lee.

## Agregar un formato

Crea una función en `senses/` con `@register("nombre", (".ext",), ("tipo/mime",))` que reciba
la ruta y devuelva una lista de `Segment(texto, {"pagina": 1, ...})`. Si no puede leer el
archivo, lanza `Unsupported("motivo para el usuario")`. Agrega una prueba en
`tests/test_senses.py` (los archivos de ejemplo se generan en `tests/fixtures.py`).
