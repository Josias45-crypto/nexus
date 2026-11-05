"""Recordatorios con el reloj REAL durante ~42 minutos, en una instancia aislada (Parte 2).

    NEXUS_E2E_IMAGE=isabelle-core:prueba python3 tests/tiempo_real.py [--minutos 42]

- Crea por el camino del bot recordatorios a +2, +3 (sin confirmar: 3 insistencias), +5, +10 y +35
  min (este tiene aviso previo a +5). Insistencias cada 5 min (NEXUS_REMINDER_RETRY_MIN=5).
- Un «canal» simulado dentro del contenedor reparte el buzón como el bot real (cada 5 s) y anota
  la hora de cada entrega; su primer envío de «Tarea05» falla como un 429 de Telegram.
- A los ~11,5 min reinicia el contenedor; de ~20 a ~25 min corta su red (OpenRouter caído) y pide
  un saludo a esa hora para que el programador intente usar la IA durante el corte.
- Al final compara cada entrega con su hora esperada (±30 s), busca duplicados y faltantes, y
  escribe tests/tiempo_real.md (ignorado por git). Usa OpenRouter real solo para crear los
  recordatorios (unos centavos); datos inventados.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.e2e import E2E_NAME, Api, isolated_instance  # noqa: E402

OUT = REPO / "tests" / "tiempo_real.md"
TOLERANCIA_S = 30
RETRY_MIN = 5
ENV = {"NEXUS_REMINDER_RETRY_MIN": str(RETRY_MIN), "NEXUS_DAILY_BUDGET_USD": ""}
TAREAS = [(2, "Tarea02"), (3, "Tarea03"), (5, "Tarea05"), (10, "Tarea10"), (35, "Tarea35")]

# Corre dentro del contenedor: reparte el buzón como el bot de Telegram y anota cada entrega
RECEPTOR = r'''
import asyncio, json, os, time
from channels.assistant import Assistant
from channels.client import NexusClient
from channels.simulado import OWNER, SimChannel
LOG = "/data/receptor.jsonl"
fallo_hecho = os.path.exists("/data/receptor.fallo")
class Canal(SimChannel):
    async def send(self, chat_id, text, buttons=None):
        global fallo_hecho
        if "Tarea05" in text and not fallo_hecho:
            fallo_hecho = True
            open("/data/receptor.fallo", "w").write("1")
            with open(LOG, "a") as f:
                f.write(json.dumps({"t": time.time(), "fallo": "429", "texto": text[:120]}) + "\n")
            raise RuntimeError("Telegram 429 simulado")
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "chat": chat_id, "texto": text[:160]}) + "\n")
        await super().send(chat_id, text, buttons)
        return [len(self.sent)]
async def main():
    ch = Canal([OWNER]); nx = NexusClient("http://localhost:8000"); bot = Assistant(ch, nx, {OWNER}, pausa=False)
    cada = float(os.getenv("NEXUS_CHANNEL_OUTBOX_SECONDS", "5"))
    while True:
        try:
            await bot.deliver_outbox()
        except Exception as e:
            with open(LOG, "a") as f:
                f.write(json.dumps({"t": time.time(), "error_receptor": type(e).__name__}) + "\n")
        await asyncio.sleep(cada)
asyncio.run(main())
'''

LEER = r'''
import json, sqlite3
c = sqlite3.connect("/data/nexus.db"); c.row_factory = sqlite3.Row
rem = [dict(r) for r in c.execute("SELECT id, text, created_at, due_at, estado, insistencias FROM reminders")]
out = [dict(r) for r in c.execute("SELECT id, ref_id, created_at, delivered_at, text FROM outbox WHERE kind = 'reminder' ORDER BY id")]
print(json.dumps({"reminders": rem, "outbox": out}))
'''


def docker(*args, check=True, **kw) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {args[0]}: {r.stderr[-300:]}")
    return r.stdout


def receptor() -> None:
    docker("exec", "-d", E2E_NAME, "python", "-c", RECEPTOR)


def esperar_salud(api: Api, max_s: int = 120) -> None:
    fin = time.monotonic() + max_s
    while time.monotonic() < fin:
        try:
            if api.call("GET", "/health")[0] == 200:
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("la instancia no volvió")


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--minutos", type=float, default=42)
    args = p.parse_args()
    eventos: list[tuple[float, str]] = []
    with isolated_instance(ENV) as url:
        api = Api(url)
        receptor()
        t0 = time.time()
        for mins, tag in TAREAS:
            r = api.ok("POST", "/conversar", {"mensajes": [f"recuérdame en {mins} minutos revisar el pedido {tag}"],
                                               "channel": "simulado", "chat_id": "1001"})
            log(f"creado {tag}: {r['texto'][:70]!r}")
        reinicio = t0 + 11.5 * 60
        corte, vuelta = t0 + 20 * 60, t0 + 25 * 60
        hecho_reinicio = hecho_corte = hecho_vuelta = False
        while time.time() < t0 + args.minutos * 60:
            ahora = time.time()
            if not hecho_reinicio and ahora >= reinicio:
                log("reinicio del contenedor")
                eventos.append((ahora, "reinicio"))
                docker("restart", "-t", "10", E2E_NAME)
                esperar_salud(api)
                receptor()
                hecho_reinicio = True
            if not hecho_corte and ahora >= corte - 60:
                # Un saludo justo durante el corte: el programador intentará usar la IA sin red
                hora = (datetime.now(timezone(timedelta(hours=-5))) + timedelta(minutes=2)).strftime("%H:%M")
                h, m = (int(x) for x in hora.split(":"))
                ampm = f"{(h % 12) or 12}:{m:02d} {'am' if h < 12 else 'pm'}"
                r = api.ok("POST", "/conversar", {"mensajes": [f"desde hoy el saludo de la noche a las {ampm}"],
                                                   "channel": "simulado", "chat_id": "1001"})
                log(f"saludo pedido a las {ampm}: {r['texto'][:60]!r}")
                time.sleep(max(0, corte - time.time()))
                log("corte de red (OpenRouter inalcanzable)")
                eventos.append((time.time(), "corte"))
                docker("network", "disconnect", "bridge", E2E_NAME)
                hecho_corte = True
            if hecho_corte and not hecho_vuelta and ahora >= vuelta:
                docker("network", "connect", "bridge", E2E_NAME)
                log("red de vuelta")
                eventos.append((time.time(), "vuelta"))
                hecho_vuelta = True
            time.sleep(5)
        entregas = [json.loads(l) for l in docker("exec", E2E_NAME, "cat", "/data/receptor.jsonl").splitlines() if l.strip()]
        datos = json.loads(docker("exec", E2E_NAME, "python", "-c", LEER))
        salud = api.ok("GET", "/health")
    return informe(t0, entregas, datos, eventos, salud)


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.split(" ")[0]).timestamp()


def informe(t0: float, entregas: list, datos: dict, eventos: list, salud: dict) -> int:
    recibidas = [e for e in entregas if "chat" in e]
    filas, problemas = [], []
    for r in sorted(datos["reminders"], key=lambda r: r["due_at"]):
        tag = next((t for _, t in TAREAS if t in r["text"]), r["text"][:20])
        mias = sorted((e for e in recibidas if tag in e["texto"]), key=lambda e: e["t"])
        due = _ts(r["due_at"])
        esperadas = []
        if due - _ts(r["created_at"]) > 30 * 60:
            esperadas.append(("previo", due - 30 * 60))
        esperadas.append(("hora", due))
        textos = [e["texto"] for e in mias]
        duplicados = len(textos) - len(set(textos))
        if duplicados:
            problemas.append(f"{tag}: {duplicados} duplicado(s)")
        anterior = None
        for i, e in enumerate(mias):
            if i < len(esperadas):
                etapa, esperado = esperadas[i]
            else:  # insistencia: RETRY_MIN después de la entrega anterior
                etapa, esperado = f"insistencia {i - len(esperadas) + 1}", anterior + RETRY_MIN * 60
            desfase = e["t"] - esperado
            ok = abs(desfase) <= TOLERANCIA_S
            if not ok:
                problemas.append(f"{tag} {etapa}: {desfase:+.0f} s")
            filas.append(f"| {tag} | {etapa} | {(esperado - t0) / 60:.2f} | {(e['t'] - t0) / 60:.2f} | {desfase:+.0f} | {'ok' if ok else 'FUERA'} |")
            anterior = e["t"]
        if not mias:
            problemas.append(f"{tag}: ninguna entrega")
    lineas = ["# Recordatorios con el reloj real", "",
              f"Inicio {datetime.fromtimestamp(t0).strftime('%Y-%m-%d %H:%M:%S')} · tolerancia ±{TOLERANCIA_S} s · "
              f"insistencias cada {RETRY_MIN} min · eventos: " + ", ".join(f"{n} a los {(t - t0) / 60:.1f} min" for t, n in eventos), "",
              "| tarea | etapa | esperado (min) | entregado (min) | desfase (s) | |", "|---|---|---|---|---|---|", *filas, "",
              f"Fallos simulados de Telegram: {sum(1 for e in entregas if 'fallo' in e)} · errores del receptor: "
              f"{sum(1 for e in entregas if 'error_receptor' in e)} · /health: recordatorios_atrasados = "
              f"{salud.get('recordatorios_atrasados')}", "",
              "Problemas: " + ("; ".join(problemas) if problemas else "ninguno")]
    OUT.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas[-3:]))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
