"""15 conversaciones de ejemplo con Isabelle (datos inventados) para juzgar el tono.

    python3 tests/muestra_conversaciones.py

Levanta una instancia aislada (como tests/e2e.py; la memoria real no se toca), le enseña unas
notas sintéticas y conversa por POST /conversar con OpenRouter real (centavos). Escribe
tests/muestra_conversaciones.md (ignorado por git) con cada turno, la acción, el nivel, el
modelo y el costo.
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.e2e import E2E_NAME, ISOLATED_ENV, Api, event_id, isolated_instance, wait_status  # noqa: E402

OUT = REPO / "tests" / "muestra_conversaciones.md"
CHAT = "muestra"
NOTAS = [
    "Tralvex es mi proveedor de laptops Lenovo. Entrega los martes y desde el martes 3 de noviembre la laptop "
    "IdeaPad 5 sube a 2 450 soles.",
    "Rosa Quispe se llevó una impresora Epson L3250 a 450 soles. Pagó 100; debe 350 y se comprometió "
    "a pagar 50 cada viernes. Ya se atrasó dos viernes.",
    "Ventas de octubre: 38 laptops, 12 impresoras y 25 audífonos. Margen promedio 18 %.",
]
# (título, [turnos]); un turno es texto, ("boton", "Cambiar") o ("foto", nombre)
CONVERSACIONES = [
    ("Saludo de la mañana", ["Buenos días, Isabelle"]),
    ("Guardar con «recuerda esto»", ["recuerda esto: el técnico Wilmer cobra 80 soles por formatear una laptop"]),
    ("Guardar sin decirlo", ["Te cuento que el local de la avenida España cierra a las 8 los sábados"]),
    ("Preguntar a la memoria", ["¿cuánto me debe Rosa Quispe?"]),
    ("No sabe algo", ["¿cuántos años tiene mi sobrino Lucas?"]),
    ("Pregunta general", ["¿qué es el CPC en publicidad?"]),
    ("Recordatorio con botones", ["recuérdame mañana a las 9 llamar a Tralvex", "de la mañana", ("boton", "Cambiar"),
                                  "mejor a las 10", "am"]),
    ("Analizar", ["si compro 20 laptops a 2 450 y las vendo a 2 900, ¿cuánto gano?"]),
    ("Analizar a fondo (modelo potente)", ["analiza a fondo si me conviene comprarle 20 laptops a Tralvex ahora que subió el precio"]),
    ("Opinar sobre marketing", ["dame tu opinión: quiero sortear unos audífonos en Instagram para ganar seguidores"]),
    ("Un riesgo", ["voy a venderle a Rosa Quispe otra impresora al crédito, total siempre paga"]),
    ("Hábitos y psicología", ["últimamente no tengo ganas de estudiar, llego cansado y me distraigo con el celular"]),
    ("¿Es una persona?", ["oye, en serio, ¿eres una persona de verdad?"]),
    ("Foto simulada", [("foto", "vitrina_nueva.png"), "¿qué te parece?"]),
    ("Despedida", ["gracias Isabelle", "ok ok", "me voy a dormir, hasta mañana"]),
]
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def subir_foto(api: Api, nombre: str) -> dict:
    r = api.upload(nombre, PNG, "image/png")
    return api.ok("POST", "/conversar/archivo",
                  {"filename": nombre, "kind": r.get("kind", "image"), "duplicate": bool(r.get("duplicate")), "chat_id": CHAT})


def main() -> int:
    with isolated_instance(ISOLATED_ENV) as url:
        api = Api(url)
        for nota in NOTAS:
            wait_status(api, event_id(api.ok("POST", "/inbox/text", {"text": nota, "source": "muestra"})), 180)
        lineas = [
            "# Muestra de conversaciones con Isabelle",
            "",
            f"Generado el {time.strftime('%Y-%m-%d %H:%M')} en una instancia aislada, con datos inventados y "
            "OpenRouter real. Debajo de cada respuesta: acción · nivel · modelo · costo.",
            "",
            "Notas que conoce: " + " / ".join(NOTAS),
        ]
        total, botones = 0.0, None
        for n, (titulo, turnos) in enumerate(CONVERSACIONES, 1):
            lineas += ["", f"## {n}. {titulo}", ""]
            for turno in turnos:
                if isinstance(turno, tuple) and turno[0] == "boton":
                    dato = next(b["dato"] for fila in botones or [] for b in fila if b["texto"] == turno[1])
                    lineas.append(f"**Jefe:** _(pulsa «{turno[1]}»)_  ")
                    r = api.ok("POST", "/conversar/boton", {"dato": dato, "channel": "muestra", "chat_id": CHAT})
                elif isinstance(turno, tuple) and turno[0] == "foto":
                    lineas.append(f"**Jefe:** _(envía la foto {turno[1]})_  ")
                    r = subir_foto(api, turno[1])
                else:
                    lineas.append(f"**Jefe:** {turno}  ")
                    t0 = time.monotonic()
                    r = api.ok("POST", "/conversar", {"mensajes": [turno], "channel": "muestra", "chat_id": CHAT})
                    r["segundos"] = time.monotonic() - t0
                botones = r.get("botones")
                llm = r.get("llm") or {}
                costo = llm.get("costo_usd") or 0.0
                total += costo
                texto = r["texto"].replace("\n", "  \n")
                lineas.append(f"**Isabelle:** {texto}  ")
                if botones:
                    lineas.append("[" + "] [".join(b["texto"] for fila in botones for b in fila) + "]  ")
                meta = [r.get("accion") or "—", r.get("nivel") or "—", llm.get("modelo") or "sin IA",
                        f"{costo:.5f} USD" if costo else "0 USD"]
                if "segundos" in r:
                    meta.append(f"{r['segundos']:.1f} s")
                lineas.append(f"<sub>{' · '.join(meta)}</sub>")
                lineas.append("")
            print(f"{n:2}. {titulo}: ok", flush=True)
        deadline = time.monotonic() + 240  # la memoria se actualiza cuando la charla se calma
        while True:
            mem = api.ok("GET", "/charla/memoria")
            if mem["resumen"] or time.monotonic() > deadline:
                break
            time.sleep(10)
        lineas += ["", "## Lo que aprendió del jefe (ficha y resumen)", "",
                   "```json", json.dumps(mem, ensure_ascii=False, indent=2), "```", "",
                   f"Costo total de la muestra: {total:.5f} USD (sin contar la memoria, que corre aparte)."]
        OUT.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        logs = subprocess.run(["docker", "logs", E2E_NAME], capture_output=True, text=True)
        print("\n".join(l for l in (logs.stdout + logs.stderr).splitlines() if "nexus.charla" in l or "memoria:" in l))
        print(f"Escrito {OUT.relative_to(REPO)} · costo {total:.5f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
