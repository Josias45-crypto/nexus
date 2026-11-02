"""Compara candidatos a modelo potente con las mismas 3 preguntas (datos inventados).

Corre dentro del contenedor (usa la clave de .env y la política zdr del cliente):
    docker compose run --rm --no-deps -v "$PWD/tests:/app/tests" core python -m tests.comparar_modelos

Escribe tests/comparacion_modelos.md (ignorado por git) con las respuestas lado a lado, el costo
real que informa OpenRouter y el tiempo de cada una. No elige: lo decide el jefe leyendo.
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from config import settings
from providers.openrouter import ChatUnavailable, OpenRouter

MODELOS = ["deepseek/deepseek-v4-pro", "anthropic/claude-sonnet-5.5", "google/gemini-3.5-flash"]
OUT = Path(__file__).resolve().parent / "comparacion_modelos.md"
SISTEMA = (
    "Eres Isabelle, secretaria personal de un ingeniero que tiene una tienda de tecnología en "
    "Trujillo, Perú. Hablas en español peruano, de usted al trabajar, claro y concreto, sin "
    "generalidades. Si ves un riesgo real, lo dices con respeto. No inventas datos."
)
PREGUNTAS = [
    ("a) Decisión de negocio", (
        "Analiza a fondo esta decisión, con pros y contras: tengo 18 000 soles ahorrados y me "
        "ofrecen traspasar un local de 40 m² en el centro por 12 000 soles de traspaso más 2 500 "
        "soles de alquiler al mes. Hoy vendo en línea unos 9 000 soles al mes con 22 % de margen y "
        "sin local. ¿Me conviene?")),
    ("b) Estrategia de marketing digital", (
        "Dame tu opinión sobre esta estrategia para mi tienda de tecnología: publicar 3 reels por "
        "semana en TikTok con 'unboxing' de laptops, gastar 600 soles al mes en anuncios de "
        "Instagram dirigidos a universitarios de 18 a 24 años y ofrecer 5 % de descuento a quien "
        "comparta la publicación. ¿Qué cambiarías?")),
    ("c) Hábito de estudio (psicología)", (
        "Piénsalo bien: quiero estudiar 1 hora diaria de marketing digital, pero llego cansado a "
        "las 9 p. m., agarro el celular y se me pasa. Ya lo intenté tres veces y lo dejo a la "
        "semana. ¿Qué me aconsejas desde la psicología de los hábitos?")),
]


async def preguntar(client: OpenRouter, modelo: str, pregunta: str) -> dict:
    t0 = time.monotonic()
    try:
        texto, info = await client.chat_ex(
            [{"role": "system", "content": SISTEMA}, {"role": "user", "content": pregunta}],
            model=modelo, temperature=0.4, max_tokens=6000,
        )
    except ChatUnavailable as exc:
        return {"texto": f"(sin respuesta: {exc})", "costo": None, "segundos": time.monotonic() - t0, "tokens": ""}
    return {
        "texto": texto.strip(),
        "costo": info["costo_usd"],
        "segundos": time.monotonic() - t0,
        "tokens": f"{info['tokens_entrada']}+{info['tokens_salida']}",
    }


async def main() -> None:
    client = OpenRouter(settings.OPENROUTER_API_KEY, settings.OPENROUTER_MODEL, None)
    resultados = {}
    for titulo, pregunta in PREGUNTAS:
        resultados[titulo] = await asyncio.gather(*(preguntar(client, m, pregunta) for m in MODELOS))
    lineas = [
        "# Comparación de modelos potentes",
        "",
        f"Fecha: {datetime.now():%Y-%m-%d %H:%M}. Política: zdr + data_collection=deny. Temperatura 0,4.",
        "Costo real informado por OpenRouter (USD). Datos inventados.",
        "",
        "## Resumen",
        "",
        "| Modelo | " + " | ".join(t for t, _ in PREGUNTAS) + " | Total USD |",
        "|---|" + "---|" * (len(PREGUNTAS) + 1),
    ]
    for i, m in enumerate(MODELOS):
        celdas, total = [], 0.0
        for titulo, _ in PREGUNTAS:
            r = resultados[titulo][i]
            total += r["costo"] or 0
            costo = f"{r['costo']:.5f}" if r["costo"] is not None else "—"
            celdas.append(f"{costo} USD · {r['segundos']:.1f} s")
        lineas.append(f"| `{m}` | " + " | ".join(celdas) + f" | {total:.5f} |")
    for titulo, pregunta in PREGUNTAS:
        lineas += ["", f"## {titulo}", "", f"> {pregunta}", ""]
        for m, r in zip(MODELOS, resultados[titulo]):
            costo = f"{r['costo']:.5f} USD" if r["costo"] is not None else "costo no informado"
            lineas += [f"### `{m}` — {costo} · {r['segundos']:.1f} s · tokens {r['tokens']}", "", r["texto"], ""]
    OUT.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"Escrito {OUT.name}")
    for titulo, _ in PREGUNTAS:
        print(titulo, [f"{m.split('/')[1]}: {r['costo']} USD {r['segundos']:.0f}s" for m, r in zip(MODELOS, resultados[titulo])])


if __name__ == "__main__":
    asyncio.run(main())
