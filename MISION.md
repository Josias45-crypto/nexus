# MISIÓN: dejar NEXUS terminado, local y profesional

Actúa como ingeniero senior y arquitecto. Piensa, razona y deduce: no ejecutes a ciegas.
Esencia (NO cambiar): cerebro personal 100% local que empieza vacío y aprende día a día; la
inteligencia vive en el conocimiento acumulado, no en el modelo; el original se guarda siempre
intacto; un módulo por sentido; el modelo es intercambiable por configuración.

## Restricciones
- Solo IA local (Ollama). Elimina cualquier rastro de Groq o nube. Sin telemetría ni servicios
  externos; las únicas descargas permitidas son los modelos, y se documentan.
- Hardware objetivo: laptop/servidor con 8 GB de RAM, sin GPU. Debe correr cómodo ahí.
- Docker Compose. Secretos solo en .env. Nunca toques data/, backups/ ni .env. Nunca
  `docker compose down -v`. Antes de cambiar el esquema de la base, haz un respaldo (POST /backup)
  y migra sin perder datos.
- Un commit por cambio que funciona (Conventional Commits). Si algo falla dos veces, para y
  explícame el dilema en vez de seguir parchando.

## Fase 0: auditoría (solo lectura, no edites nada)
Lee CLAUDE.md, README.md, todo core/, providers/, config/, tests/ y docker-compose.yml.
Ejecuta `python3 tests/e2e.py` y revisa `docker compose logs`. Entrégame en máx. 40 líneas:
errores reales, riesgos (concurrencia de SQLite, límites de tamaño, nombres de archivo,
bloqueos del worker, recursos), deuda técnica y qué falta frente al diseño original.
Entre lo pendiente conocido: /reindex, cola separada para audios largos, timestamps en audio,
recalibrar NEXUS_MAX_DISTANCE con datos reales, preguntas temporales ("lo último que dije",
"qué aprendí hoy"), citas más fiables, digestión lenta de libros grandes, Ollama fijado a una versión.
Propón el plan por fases ordenado por impacto y espera mi OK solo en este punto.

## Fase 1: robustez
Corrige lo encontrado. SQLite con WAL y busy_timeout; healthchecks y depends_on con condición en
compose; límites de tamaño y validaciones; errores claros en español; logging útil; apagado
limpio; reintentos y /requeue para todo; fijar versiones (incluida la imagen de Ollama).

## Fase 2: archivos de todo tipo
Registro de extractores en senses/: cada formato implementa extract(path, mime) y devuelve
segmentos con texto y metadatos (página, hoja, marca de tiempo). Soporta como mínimo: txt, md,
csv, json, xml, html, código fuente, pdf (con OCR local de respaldo cuando es escaneado),
docx, xlsx, pptx, epub, eml, imágenes (OCR local; descripción con un modelo de visión local solo si
es opcional y viene apagada por defecto), audio (ya existe) y video (extraer el audio y
transcribirlo), y zip (procesar su contenido). Un formato desconocido NO se pierde: se guarda el
original, se marca "no soportado" con motivo, y se puede reprocesar cuando exista el extractor.
Dependencias pesadas solo si caben en 8 GB; documenta el tamaño final de la imagen.

## Fase 3: calidad del conocimiento
Troceado que respete estructura (títulos, párrafos, hojas, diapositivas). /reindex que regenera
embeddings desde los originales sin perder nada. Digestión eficiente para documentos grandes
(por capítulos o secciones, con progreso visible y límite configurable). Preguntas temporales y
meta ("qué aprendí hoy", "qué sabes de X", "resume lo último que subí"). Respuestas con citas
fiables armadas por el código, no por el modelo. Recalibra el umbral con datos reales y
documenta cómo.

## Fase 4: experiencia
La interfaz web (/) y el cerebro en vivo (/brain) estables, claros y sin consumo excesivo:
progreso de procesamiento, errores visibles y reintento desde la interfaz. Descubrimiento de
fallos sin abrir la terminal.

## Fase 5: pruebas y documentación
Amplía tests/e2e.py: un caso por cada tipo de archivo con fixtures sintéticos, preguntas
temporales, /reindex, caos (Ollama caído) y audio. Documentación profesional en español:
README.md (visión, inicio rápido, estado), docs/ARQUITECTURA.md (con diagrama), docs/API.md,
docs/FORMATOS.md, docs/OPERACION.md (respaldos, restauración, actualización de modelos,
solución de problemas, cambio a un modelo mayor con NEXUS_LLM_MODEL), CHANGELOG.md y CLAUDE.md
actualizado. Prueba final obligatoria: clonar el repo en /tmp, `cp .env.example .env`,
`docker compose up -d --build` y pasar el e2e completo.

## Definición de terminado
e2e en verde (incluido audio y caos); clon limpio funcionando; docs completas; sin Groq ni
nube; imagen y consumo de RAM documentados; todo commiteado. Al terminar cada fase: ejecuta el
e2e, haz commit y resúmeme en máx. 8 líneas qué cambió y qué decidiste. Usa /compact entre fases.
