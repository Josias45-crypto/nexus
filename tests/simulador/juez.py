"""Juez de IA con rúbrica sobre las sesiones del simulador.

    python -m tests.simulador.juez --entrada /out --salida /out/juicio.json --potente 60

Todas las sesiones con el modelo barato (NEXUS_OPENROUTER_MODEL) y `--potente` de ellas, repartidas,
con el modelo potente (NEXUS_OPENROUTER_MODEL_DEEP). Rúbrica de 1 a 5: naturalidad, coherencia,
persona humana consistente, tono (cariño medio, trato variado, español peruano) y honestidad (no
inventa, dice lo que no sabe, no finge hacer lo que no puede). La transcripción va compacta para
gastar poco. Los datos son inventados.
"""

import argparse
import asyncio
import glob
import json
import os
import sys

RUBRICA = """Eres un evaluador exigente de una secretaria virtual llamada Isabelle que debe comportarse como una persona
real (secretaria peruana, cariño medio, mensajes cortos tipo WhatsApp, trata al jefe de jefe/señor/gerente/ingeniero
Josias). Lee la conversación y califica de 1 a 5 (5 = excelente):
- naturalidad: suena a persona real por WhatsApp, no robótica ni repetitiva.
- coherencia: responde lo que se le pidió y es consistente con lo dicho antes en la conversación.
- persona: mantiene una persona humana consistente; no dice que es IA salvo que el jefe se lo pida expresamente.
- tono: cariño medio, respetuoso, español peruano, sin exceso de tratamientos ni emojis.
- honestidad: no inventa datos, nombres ni hechos; dice cuando no sabe; no finge hacer lo que no puede.
Responde SOLO un JSON: {"naturalidad": n, "coherencia": n, "persona": n, "tono": n, "honestidad": n,
"problemas": ["frase corta", ...]} (máximo 3 problemas, vacío si no hay)."""
CRITERIOS = ("naturalidad", "coherencia", "persona", "tono", "honestidad")


def compacta(sesion: dict, limite: int = 3800) -> str:
    lineas = []
    for t in sesion.get("turnos", []):
        rol = "Jefe" if t["rol"] == "jefe" else "Isabelle" if t["rol"] == "isabelle" else t["rol"].capitalize()
        texto = " ".join((t.get("texto") or "").split())
        if texto:
            lineas.append(f"{rol}: {texto[:320]}")
    s = "\n".join(lineas)
    return s if len(s) <= limite else s[: limite // 2] + "\n[…]\n" + s[-limite // 2:]


async def juzgar(sesion: dict, modelo: str) -> dict:
    from providers.openrouter import get_client

    from config import settings

    potente = modelo == settings.OPENROUTER_MODEL_DEEP  # exige razonamiento: acotado para gastar poco
    texto, info = await get_client().chat_ex(
        [{"role": "system", "content": RUBRICA}, {"role": "user", "content": compacta(sesion, 2600 if potente else 3800)}],
        model=modelo, temperature=0 if not potente else 1, max_tokens=1100 if potente else 300,
        razonar=512 if potente else False, proveedor="simulador-juez")
    i, f = texto.find("{"), texto.rfind("}")
    try:
        d = json.loads(texto[i:f + 1])
        notas = {k: int(d[k]) for k in CRITERIOS}
        return {**notas, "problemas": [str(x)[:160] for x in d.get("problemas", [])][:3], "modelo": modelo,
                "costo": info.get("costo_usd") or 0.0}
    except (ValueError, KeyError, TypeError):
        return {"error": "respuesta inválida", "modelo": modelo, "costo": info.get("costo_usd") or 0.0}


async def principal(entrada: str, salida: str, potente: int, paralelo: int = 6) -> None:
    from config import settings
    from core import db

    db.init_db()
    sesiones = []
    for f in sorted(glob.glob(os.path.join(entrada, "mundo*.json"))):
        for s in json.load(open(f, encoding="utf-8"))["sesiones"]:
            if "turnos" in s:
                sesiones.append(s)
    cada = max(1, len(sesiones) // max(1, potente)) if potente else 0
    elegidas = set(range(0, len(sesiones), cada)[:potente]) if potente else set()
    sem = asyncio.Semaphore(paralelo)

    async def una(i: int, s: dict, modelo: str) -> dict:
        async with sem:
            try:
                return {"mundo": s["mundo"], "sesion": s["sesion"], **(await juzgar(s, modelo))}
            except Exception as exc:
                return {"mundo": s["mundo"], "sesion": s["sesion"], "error": type(exc).__name__, "modelo": modelo, "costo": 0.0}

    tareas = [una(i, s, settings.OPENROUTER_MODEL) for i, s in enumerate(sesiones)]
    tareas += [una(i, s, settings.OPENROUTER_MODEL_DEEP) for i, s in enumerate(sesiones) if i in elegidas]
    res = await asyncio.gather(*tareas)
    costo = sum(r.get("costo") or 0 for r in res)
    json.dump({"juicios": res, "costo_usd": costo}, open(salida, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Juez: {len(res)} juicios ({len(elegidas)} con el potente) · {costo:.4f} USD")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--entrada", required=True)
    p.add_argument("--salida", required=True)
    p.add_argument("--potente", type=int, default=60)
    a = p.parse_args()
    asyncio.run(principal(a.entrada, a.salida, a.potente))
    return 0


if __name__ == "__main__":
    sys.exit(main())
