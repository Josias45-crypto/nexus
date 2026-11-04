"""Corre el simulador: varios mundos en paralelo (cada uno en su contenedor desechable) y el juez.

    NEXUS_E2E_IMAGE=isabelle-core:prueba python3 tests/simulador/correr.py --mundos 5 --sesiones 60 --tope-usd 2
    python3 tests/simulador/correr.py --piloto          # 1 mundo, 6 sesiones: mide costo y tiempo

Cada mundo corre en `docker run --rm` con su propia carpeta de datos temporal (la memoria real no se
toca), la clave de .env y el código de tests/ montado en solo lectura. Freno de gasto: cada mundo
recibe tope-usd / mundos y no empieza sesiones nuevas si lo pasa. El resultado queda en
tests/simulador/salida/<fecha>/ (ignorado por git): un JSON por mundo y juicio.json.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SALIDA = REPO / "tests" / "simulador" / "salida"


def imagen() -> str:
    return os.environ.get("NEXUS_E2E_IMAGE") or "isabelle-core:prueba"


def contenedor(nombre: str, datos: Path, salida: Path, *comando: str) -> list[str]:
    return ["docker", "run", "--rm", "--name", nombre, "--env-file", str(REPO / ".env"),
            "-e", "NEXUS_DATA_DIR=/data", "-e", "NEXUS_BACKUP_HOURS=0", "-e", "NEXUS_WORKER=off",
            "-e", "NEXUS_DAILY_BUDGET_USD=", "-e", "NEXUS_INTERNAL_TOKEN=simulador-" + nombre,
            "-v", f"{datos}:/data", "-v", f"{REPO / 'tests'}:/app/tests:ro", "-v", f"{salida}:/out",
            imagen(), "python", "-m", *comando]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mundos", type=int, default=5)
    p.add_argument("--sesiones", type=int, default=60, help="por mundo")
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--tope-usd", type=float, default=2.0, help="para todos los mundos (el juez aparte)")
    p.add_argument("--potente", type=int, default=60, help="sesiones que juzga el modelo potente")
    p.add_argument("--piloto", action="store_true")
    p.add_argument("--sin-juez", action="store_true")
    p.add_argument("--solo", help="lista de escenarios (para repetir lo fallado), p. ej. rec_tarde,correccion")
    a = p.parse_args()
    if a.piloto:
        a.mundos, a.sesiones, a.potente = 1, 6, 2
    salida = SALIDA / time.strftime("%Y%m%d-%H%M%S")
    salida.mkdir(parents=True)
    procesos, t0 = [], time.monotonic()
    for i in range(1, a.mundos + 1):
        datos = Path(tempfile.mkdtemp(prefix=f"isabelle-sim{i}-"))
        cmd = contenedor(f"isabelle-sim{i}", datos, salida, "tests.simulador.sesiones", "--mundo", str(i),
                         "--sesiones", str(a.sesiones), "--dias", str(a.dias), "--tope-usd", f"{a.tope_usd / a.mundos:.4f}",
                         "--salida", f"/out/mundo{i}.json", *(["--solo", a.solo] if a.solo else []))
        subprocess.run(["docker", "rm", "-f", f"isabelle-sim{i}"], capture_output=True)
        log = open(salida / f"mundo{i}.log", "w")
        procesos.append((i, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), datos))
    for i, pr, datos in procesos:
        pr.wait()
        print(f"mundo {i}: salida {pr.returncode} · {(time.monotonic() - t0) / 60:.1f} min", flush=True)
    if not a.sin_juez:
        datos = Path(tempfile.mkdtemp(prefix="isabelle-juez-"))
        subprocess.run(contenedor("isabelle-juez", datos, salida, "tests.simulador.juez", "--entrada", "/out",
                                  "--salida", "/out/juicio.json", "--potente", str(a.potente)))
    gasto = sum(json.load(open(f))["gasto_usd"] for f in salida.glob("mundo*.json"))
    juez = json.load(open(salida / "juicio.json"))["costo_usd"] if (salida / "juicio.json").exists() else 0.0
    print(f"Listo en {(time.monotonic() - t0) / 60:.1f} min · mundos {gasto:.4f} USD · juez {juez:.4f} USD · {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
