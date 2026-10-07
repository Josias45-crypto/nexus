# Perfiles de rubro

El núcleo de NEXUS es genérico. Lo propio de cada negocio (cómo se llama el asistente, su
personalidad, el contexto, la hora del resumen matutino) vive en un archivo
`profiles/<nombre>.toml`, que se elige con `NEXUS_PROFILE` en `.env`.

Incluidos:
- `general`: memoria personal (por defecto).
- `ventas`: dueño de un negocio de ventas (clientes, cotizaciones, cobranzas).

Para cambiar de perfil, edita `NEXUS_PROFILE` y reinicia con
`docker compose up -d --force-recreate core`. Si el perfil no existe o tiene un error, NEXUS
no arranca y el registro dice por qué.

## Campos

```toml
[asistente]
nombre = "NEXUS Taller"         # cómo se presenta
idioma = "es"
tono = "técnico y amable"
persona = """Quién es y qué hace por el dueño (va al inicio de cada instrucción al modelo)."""

[negocio]
descripcion = "Una línea con el contexto del negocio."

[entidades]
# Tipos de cosas del negocio (los usará la extracción experimental de la Fase 5C)
categorias = ["clientes", "vehículos", "órdenes", "repuestos"]

[proactivo]
resumen_matutino = "07:30"      # HH:MM hora local; "" para desactivar
seguimiento_dias = 5            # sugerir seguimiento tras N días sin contacto (requiere 5C)

[ejemplos]
preguntas = ["¿Qué le hicimos al auto de Rosa?", "Recuérdame el lunes pedir pastillas de freno"]
```

Los campos que no pongas toman el valor por defecto. `GET /profile` muestra el perfil activo.

## Crear uno nuevo

1. Copia `profiles/general.toml` a `profiles/<rubro>.toml`. El nombre del archivo solo admite
   minúsculas, números, `-` y `_`.
2. Escribe la `persona` como si le explicaras el trabajo a un asistente nuevo: a quién ayuda,
   qué debe recordar y qué **no** hace (por ejemplo, "no hablas con los pacientes").
3. Pon `NEXUS_PROFILE=<rubro>` en `.env`, reinicia con
   `docker compose up -d --force-recreate core` y prueba con `/ayuda` en Telegram. La carpeta
   `profiles/` está montada en el contenedor, así que no hace falta reconstruir la imagen.

Ejemplos de `persona`:

- **Clínica:** "Eres el asistente del médico titular. Le ayudas a recordar citas, indicaciones
  y pendientes administrativos. No das diagnósticos ni hablas con pacientes." Marca como
  `/privado` todo dato de salud.
- **Taller mecánico:** "Eres el asistente del dueño del taller. Recuerdas qué se le hizo a cada
  vehículo, presupuestos y repuestos por pedir."
- **Estudio contable:** "Eres el asistente del contador. Recuerdas vencimientos tributarios de
  cada cliente, documentos pendientes y acuerdos de honorarios."

## Qué no va en un perfil

Las claves, tokens y URLs van en `.env`. La lógica nueva (por ejemplo, otro tipo de aviso) va
en el núcleo y debe servir a cualquier rubro.
