# Formatos que entiende NEXUS

Todo lo que llega a la bandeja (web, `POST /inbox/file`, Telegram) se guarda **intacto** en
`data/raw`. Después, un *sentido* (`senses/`) saca el texto con su origen, lo trocea e indexa.
Todo es local: el OCR, la transcripción y la visión no salen de la máquina.

| Tipo | Extensiones | Qué se extrae | Origen en la cita |
|---|---|---|---|
| Texto | .txt .md .markdown .log .rst .org (y archivos sin extensión que sean texto) | Todo; en Markdown, los `#` marcan secciones | `§ Sección` |
| Código y configuración | .py .js .ts .java .go .rs .c .cpp .cs .rb .php .sh .sql .css .yaml .toml .ini … | Todo, sin tomar `# comentarios` como secciones | — |
| Tablas | .csv .tsv (detecta `,` `;` tab `\|`) | Cada fila como `columna: valor; …` | `fila 2–40` |
| JSON | .json .jsonl .ndjson .geojson | `ruta.de.la.clave: valor` | — |
| XML | .xml .svg .plist .gpx .kml | `etiqueta: texto` | — |
| Web | .html .htm .xhtml | Texto visible; los títulos `<h1>`… como secciones; sin scripts ni estilos | `§ Sección` |
| PDF | .pdf | Texto por página; **páginas escaneadas: OCR** (hasta `NEXUS_OCR_MAX_PAGES`) | `pág. 3–4` |
| Word | .docx .docm .dotx | Párrafos, títulos (como secciones), listas y tablas | `§ Sección` |
| Excel | .xlsx .xlsm | Cada hoja; cada fila como `encabezado: valor; …` | `hoja Ventas, fila 2–30` |
| PowerPoint | .pptx .pptm .ppsx | Texto de cada diapositiva en orden de presentación, más sus notas | `diap. 5` |
| Libros | .epub | Capítulos en orden de lectura | `cap. 3` |
| Correo | .eml | Asunto, remitente, destinatarios, fecha, cuerpo y **adjuntos legibles** | `adjunto.pdf, pág. 2` |
| Imágenes | .png .jpg .jpeg .webp .gif .bmp .tif .tiff | **OCR** del texto visible; descripción opcional (ver abajo) | — (TIFF de varias páginas: `pág. N`) |
| Audio | .mp3 .wav .m4a .ogg .oga .opus .flac .aac .wma .amr | Transcripción (faster-whisper) | `min 01:23` |
| Video | .mp4 .mkv .mov .webm .avi .m4v .3gp .mpeg .mpg .wmv | Transcripción del audio | `min 01:23` |
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

Dentro de un .zip o de un correo se omiten los audios y videos (súbelos aparte: van a la cola
de audio) y lo que no se pueda leer; el resto sí se indexa.

Estados sin texto (`empty`): una imagen sin texto visible, un video sin pista de audio o un
audio sin voz. El motivo aparece en la web y en `GET /inbox`.

## OCR (Tesseract)

- Viene en la imagen de Docker con español e inglés (`NEXUS_OCR_LANGS=spa+eng`). Para otros
  idiomas hay que agregar el paquete `tesseract-ocr-<idioma>` en el `Dockerfile`.
- Ocupa espacio: la imagen `nexus-core` pasó de 854 MB a 1,03 GB con Tesseract, Pillow y
  pypdfium2 (medido con `docker images`).
- Se aplica a imágenes y a las páginas de PDF con menos de 25 caracteres de texto. Una página
  tarda unos segundos en CPU. `NEXUS_OCR=off` lo apaga.
- Los trozos que vienen de OCR llevan `"ocr": true` en sus metadatos. El OCR se equivoca con
  letra manuscrita, fotos torcidas o con poca luz.

## Visión (opcional, apagada)

Con `NEXUS_VISION_MODEL=<modelo>` NEXUS además **describe** cada imagen con un modelo de visión
de Ollama local (útil para fotos sin texto: un producto, una pizarra, un local). Nunca usa la
nube, aunque el modo nube esté encendido.

1. Descarga un modelo de visión en el Ollama local: `docker exec nexus-ollama ollama pull <modelo>`.
2. Pon `NEXUS_VISION_MODEL=<modelo>` en `.env` y reinicia: `docker compose up -d core`.

Es lento en CPU (decenas de segundos por foto) y ocupa varios GB de RAM: pruébalo antes de
dejarlo encendido. Si falla, la imagen se indexa solo con el OCR.

## Límites

| Variable | Por defecto | Qué limita |
|---|---|---|
| `NEXUS_MAX_UPLOAD_MB` | 50 | Tamaño de cada archivo recibido (Telegram: 20 MB) |
| `NEXUS_MAX_TEXT_CHARS` | 2 000 000 | Texto indexado por elemento; el resto se ignora (se registra un aviso) |
| `NEXUS_OCR_MAX_PAGES` | 50 | Páginas escaneadas con OCR por PDF |
| `NEXUS_ARCHIVE_MAX_FILES` | 200 | Archivos leídos de un .zip |
| `NEXUS_ARCHIVE_MAX_MB` | 200 | MB descomprimidos leídos de un .zip (protege de "zips bomba") |

Las fechas de Excel aparecen como número de serie (p. ej. `45930`): el archivo no guarda el
formato de fecha en la celda, sino en el estilo, que NEXUS no lee.

## Agregar un formato

Crea una función en `senses/` con `@register("nombre", (".ext",), ("tipo/mime",))` que reciba
la ruta y devuelva una lista de `Segment(texto, {"pagina": 1, ...})`. Si no puede leer el
archivo, lanza `Unsupported("motivo para el usuario")`. Agrega una prueba en
`tests/test_senses.py` (los archivos de ejemplo se generan en `tests/fixtures.py`).
