# NEXUS

Cerebro personal **100 % local** que empieza vacío y acumula conocimiento día a día: textos y audio hoy, imágenes y más sentidos mañana. Corre en Docker y no envía tus datos a la nube.

> **Estado:** en construcción (pasos 1 a 3 de 10). Consulta la [hoja de ruta](#hoja-de-ruta).

## Idea central

- **La inteligencia vive en el conocimiento acumulado**, no en el modelo. Aunque el modelo sea pequeño, NEXUS puede responder con lo que ha absorbido.
- **El original se guarda siempre intacto.** Lo derivado (resúmenes, embeddings) se puede regenerar con un modelo mejor.
- **El modelo es intercambiable por configuración.** Local por defecto; las variantes con API se activan sin tocar el núcleo.
- **Un módulo por sentido** (texto, audio, imagen...), todos conectados a la misma memoria.

## Arquitectura

```
  Cliente (curl, apps, canales futuros)
              │  HTTP
              ▼
      ┌───────────────┐   /api/chat   ┌───────────────┐
      │  nexus-core   │ ────────────▶ │    Ollama     │
      │   (FastAPI)   │               │ (modelo local)│
      └───────┬───────┘               └───────────────┘
              │
       ./data  (SQLite + archivos originales)
```

## Requisitos

- Docker con Compose v2 (`docker compose`)
- Linux, macOS o Windows con WSL2
- ~6 GB de disco libre (imágenes + modelo)
- 8 GB de RAM recomendados

## Inicio rápido

```bash
git clone https://github.com/Josias45-crypto/nexus.git
cd nexus
cp .env.example .env
docker compose up -d --build
docker exec nexus-ollama ollama pull qwen2.5:1.5b
curl http://localhost:8000/health
```

La primera vez se descarga la imagen de Ollama (~3,8 GB) y luego el modelo (~1 GB). Si se interrumpe, repite el comando: continúa donde quedó.
Para usar **audio**, la primera transcripción requiere descargar el modelo de voz Whisper (~500 MB, una sola vez; después funciona sin internet).

## Uso

Documentación interactiva de la API: <http://localhost:8000/docs>

| Método | Ruta      | Descripción                         |
|--------|-----------|-------------------------------------|
| GET    | `/health` | Estado del servicio y modelo activo |
| POST   | `/chat`   | Conversa con el modelo configurado  |

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hola, ¿quién eres?"}'
```

## Configuración

Se define en `.env` (plantilla en `.env.example`).

| Variable             | Valor por defecto        | Descripción                                    |
|----------------------|--------------------------|------------------------------------------------|
| `NEXUS_LLM_PROVIDER` | `ollama`                 | Proveedor de modelo                            |
| `OLLAMA_BASE_URL`    | `http://ollama:11434`    | Dirección de Ollama                            |
| `NEXUS_LLM_MODEL`    | `qwen2.5:1.5b`           | Modelo de lenguaje                             |
| `NEXUS_EMBED_MODEL`  | `nomic-embed-text`       | Modelo de embeddings (se usa desde el Paso 5)  |
| `NEXUS_MAX_DISTANCE` | `0.8` | Distancia máxima para considerar relevante un recuerdo |
| `NEXUS_WORKER` | `on` | Procesamiento automático en segundo plano |
| `NEXUS_WORKER_INTERVAL` | `20` | Segundos entre revisiones de la bandeja |
| `NEXUS_MAX_ATTEMPTS` | `3` | Intentos antes de marcar un elemento como fallido |
| `NEXUS_DIGEST` | `on` | Resúmenes y conceptos automáticos por documento |
| `NEXUS_DIGEST_MIN_CHARS` | `1200` | Tamaño mínimo para digerir un documento |
| `NEXUS_WHISPER_MODEL` | `small` | Modelo de voz (`base` es más rápido, `small` más preciso) |
| `NEXUS_WHISPER_LANG` | `es` | Idioma del audio (vacío = detectar solo) |
| `NEXUS_DATA_DIR`     | `/data`                  | Carpeta de datos dentro del contenedor         |

**Cambiar de modelo:** edita `NEXUS_LLM_MODEL` en `.env`, descárgalo con `docker exec nexus-ollama ollama pull NOMBRE` y ejecuta `docker compose up -d --force-recreate core`.

**Usar un Ollama en otra máquina** (por ejemplo, un PC con GPU): cambia `OLLAMA_BASE_URL` a su dirección.

## Estructura del proyecto

```
nexus/
├── core/                 # Aplicación FastAPI y lógica central
├── providers/            # Modelos intercambiables (interfaz + implementaciones)
├── config/               # Lectura de configuración
├── senses/               # Un módulo por sentido (texto, audio...)
├── tests/                # Pruebas
├── data/                 # Memoria de NEXUS (no se sube a git)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Comandos útiles

```bash
docker compose ps                  # estado de los servicios
docker compose logs -f core        # logs en vivo
docker compose down                # detener (conserva modelos y datos)
docker compose up -d --build core  # reconstruir tras cambiar código
```

> Evita `docker compose down -v`: borra los volúmenes, incluidos los modelos descargados.

## Datos y privacidad

- Todo se procesa y guarda en tu máquina. Con el proveedor `ollama`, ningún dato sale de ella.
- `data/` y `.env` están en `.gitignore`: tu conocimiento y tu configuración nunca se suben al repositorio.
- Haz copias de seguridad de `data/` periódicamente (se automatizará en el Paso 9).

## Solución de problemas

- **`curl` falla justo después de `up`:** el servicio tarda unos segundos en arrancar. Espera 5 s y reintenta.
- **`env file .env not found`:** falta ejecutar `cp .env.example .env`.
- **Error de permisos en `data/` (Linux):** el contenedor usa el usuario con UID 1000. Si el tuyo es distinto, ejecuta `sudo chown -R 1000:1000 data`.
- **El puerto 8000 está ocupado:** cambia `127.0.0.1:8000:8000` en `docker-compose.yml`.
- **Respuestas lentas:** es normal en CPU. Usa un modelo más pequeño o apunta a un Ollama con GPU.

## Hoja de ruta

- [x] 1. Repositorio y base profesional
- [x] 2. Esqueleto en Docker (FastAPI + Ollama)
- [x] 3. Proveedor de modelo intercambiable y `/chat`
- [x] 4. Bandeja de entrada (guardar originales)
- [x] 5. Ingesta de texto y búsqueda semántica
- [x] 6. Preguntar con fuentes
- [x] 7. Digestión en segundo plano (resúmenes y conceptos)
- [x] 8. Sentido del oído (audio)
- [x] 9. Contador de crecimiento y respaldos
- [ ] 10. Despliegue en servidor y GPU remota

## Contribuir

Proyecto personal. Los commits siguen [Conventional Commits](https://www.conventionalcommits.org/es/) (`feat:`, `fix:`, `docs:`, `chore:`), con un commit por cada cambio que funciona.

## Licencia

Uso personal. Todos los derechos reservados.
