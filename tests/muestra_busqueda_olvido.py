"""10 conversaciones de ejemplo de la Fase 2E (búsqueda en internet y «olvida eso"), datos inventados.

    NEXUS_E2E_IMAGE=nexus-core:prueba python3 tests/muestra_busqueda_olvido.py

Levanta una instancia aislada (como tests/e2e.py; la memoria real no se toca), le enseña unas notas
sintéticas y conversa por POST /conversar (y /conversar/seguir tras el aviso, como el bot). Usa
OpenRouter real: 3 búsquedas en internet (unos 0,02 USD). Escribe tests/muestra_busqueda_olvido.md
(ignorado por git).
"""

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.e2e import E2E_NAME, ISOLATED_ENV, Api, event_id, isolated_instance, wait_status  # noqa: E402

OUT = REPO / "tests" / "muestra_busqueda_olvido.md"
CHAT = "muestra"
NOTAS = [
    "Rosa Quispe se llevó una impresora Epson L3250 a 450 soles; debe 350 y paga 50 cada viernes.",
    "Tralvex es mi proveedor de laptops Lenovo y entrega los martes.",
    "Tralvex subió la IdeaPad 5 a 2 450 soles desde noviembre.",
    "El local nuevo sería en la avenida España, el alquiler pedido es 1 800 soles.",
]
# (título, turnos); "TOPE" llena el contador de búsquedas del día antes del turno siguiente
CONVERSACIONES = [
    ("Fuera del cerebro: aviso, fuentes y «sí, guárdalo»", ["¿Cuál es el tipo de cambio del dólar hoy en Perú?", "sí, guárdalo"]),
    ("Búsqueda pedida y «no» a guardar", ["Busca en internet desde cuándo es obligatoria la factura electrónica en Perú", "no, gracias"]),
    ("No responde la pregunta de guardar (se descarta, nada se traga)",
     ["busca en internet el horario de atención de SUNAT", "recuérdame mañana a las 10 am llamar al contador"]),
    ("Se responde sin internet (saber general)", ["¿Qué es el margen de contribución?"]),
    ("Se responde desde el cerebro", ["¿cuánto me debe Rosa Quispe?"]),
    ("«No busques en internet»", ["¿Quién es el actual presidente del BCRP? no busques en internet"]),
    ("Tope de búsquedas del día", ["TOPE", "busca en internet el precio del cobre hoy"]),
    ("«Olvida lo de Rosa» confirmado", ["olvida lo de Rosa", "sí"]),
    ("«Olvida» ambiguo: elige de la lista", ["borra lo de Tralvex", "la 1", "sí"]),
    ("«Olvida» cancelado", ["elimina la nota del local nuevo", "no, mejor déjalo"]),
]


def llenar_tope() -> None:
    subprocess.run(
        ["docker", "exec", E2E_NAME, "python", "-c",
         "from core import clock, db, internet\n"
         "with db.connect() as c:\n"
         "    for _ in range(internet.config()['busquedas_dia'] - internet.hechas_hoy()):\n"
         "        c.execute('INSERT INTO busquedas (created_at, dia, fuentes) VALUES (?, ?, 0)',"
         " (clock.now_utc().isoformat(), clock.now_local().date().isoformat()))"],
        check=True, capture_output=True)


def main() -> int:
    with isolated_instance(ISOLATED_ENV) as url:
        api = Api(url)
        for nota in NOTAS:
            wait_status(api, event_id(api.ok("POST", "/inbox/text", {"text": nota, "source": "muestra"})), 180)
        lineas = ["# Muestra: búsqueda en internet y «olvida eso»", "",
                  f"Generado el {time.strftime('%Y-%m-%d %H:%M')} en una instancia aislada, con datos inventados y "
                  "OpenRouter real. Debajo de cada respuesta: acción · nivel · costo.", "",
                  "Notas que conoce: " + " / ".join(NOTAS)]
        total = 0.0
        for n, (titulo, turnos) in enumerate(CONVERSACIONES, 1):
            lineas += ["", f"## {n}. {titulo}", ""]
            for turno in turnos:
                if turno == "TOPE":
                    llenar_tope()
                    lineas.append("_(el contador del día llega a 20 búsquedas)_  ")
                    continue
                lineas.append(f"**Jefe:** {turno}  ")
                respuestas = [api.ok("POST", "/conversar", {"mensajes": [turno], "channel": "muestra", "chat_id": CHAT})]
                while respuestas[-1].get("seguir"):  # como el bot: el aviso sale aparte y luego la búsqueda
                    respuestas.append(api.ok("POST", "/conversar/seguir", {"channel": "muestra", "chat_id": CHAT}))
                for r in respuestas:
                    costo = (r.get("llm") or {}).get("costo_usd") or 0.0
                    total += costo
                    lineas.append(f"**Isabelle:** {r['texto'].replace(chr(10), '  ' + chr(10))}  ")
                    lineas.append(f"<sub>{r.get('accion') or '—'} · {r.get('nivel') or '—'} · {costo:.5f} USD</sub>")
                    lineas.append("")
            print(f"{n:2}. {titulo}: ok", flush=True)
        uso = api.ok("GET", "/usage")
        lineas += ["", "## Cifras", "",
                   f"- Búsquedas reales: 3 (el contador del día marca {uso['busquedas_internet_hoy']} por el tope simulado).",
                   f"- Costo de la muestra: {total:.5f} USD (/usage del día: {uso['gastado_hoy_usd']:.5f} USD)."]
        OUT.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        print(f"Escrito {OUT.relative_to(REPO)} · costo {total:.5f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
