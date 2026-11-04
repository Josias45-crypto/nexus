#!/usr/bin/env bash
# Diagnóstico de recordatorios en el servidor (Parte 2). SOLO LECTURA y sin el texto de ningún
# mensaje: horas, estados, ids y errores. Se corre EN EL SERVIDOR, en la carpeta del proyecto:
#
#   cd ~/isabelle && scripts/diagnostico_remoto.sh
#
# Imprime: hora y zona del servidor y del contenedor, estado del programador (/health), los
# últimos 20 eventos de recordatorios, el buzón pendiente y los errores de Telegram de 24 h.
set -euo pipefail
cd "$(dirname "$0")/.."

linea() { printf '\n== %s ==\n' "$1"; }

linea "Hora y zona"
echo "servidor:   $(date '+%F %T %Z (UTC%z)') · timedatectl: $(timedatectl show -p Timezone --value 2>/dev/null || echo '?')"
echo "contenedor: $(docker compose exec -T core date '+%F %T %Z' 2>/dev/null || echo 'core no responde')"
echo "NEXUS_TZ_OFFSET: $(grep -E '^NEXUS_TZ_OFFSET=' .env 2>/dev/null | cut -d= -f2 || true) (vacío = -5, Perú)"

linea "Contenedores"
docker compose ps --format '{{.Service}}: {{.Status}}' 2>/dev/null || docker compose ps

linea "Programador (/health, sin datos de mensajes)"
docker compose exec -T core python - <<'PY'
import json, urllib.request
from core import auth
req = urllib.request.Request("http://localhost:8000/health", headers={auth.CABECERA: auth.token_interno()})
d = json.load(urllib.request.urlopen(req, timeout=10))
print(json.dumps({"status": d.get("status"), "recordatorios_atrasados": d.get("recordatorios_atrasados"),
                  "programador": d.get("programador")}, ensure_ascii=False, indent=1))
w = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8000/worker", headers={auth.CABECERA: auth.token_interno()}), timeout=10))
print("latido:", w.get("latido_programador"), "· último aviso:", w.get("ultimo_aviso"), "· vigilante:", w.get("vigilante"))
PY

linea "Últimos 20 recordatorios (sin el texto)"
docker compose exec -T core python - <<'PY'
import sqlite3
from core import clock, db
c = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True); c.row_factory = sqlite3.Row
loc = lambda s: clock.utc_to_local(s).strftime("%m-%d %H:%M:%S") if s else "—"
print(f"{'id':>5} {'estado':14} {'hora (local)':15} {'próximo envío':15} {'último envío':15} ins")
for r in c.execute("SELECT id, estado, due_at, proximo_envio, last_sent_at, insistencias FROM reminders ORDER BY id DESC LIMIT 20"):
    print(f"{r['id']:>5} {r['estado']:14} {loc(r['due_at']):15} {loc(r['proximo_envio']):15} {loc(r['last_sent_at']):15} {r['insistencias']}")
print("\nAvisos de recordatorios en el buzón (últimos 20, sin el texto):")
print(f"{'buzón':>6} {'rec':>5} {'salió (local)':15} {'entregado':15} {'demora':>7} {'intentos':>8} acuse / error")
for r in c.execute("SELECT id, ref_id, created_at, delivered_at, intentos, acuse, ultimo_error FROM outbox"
                   " WHERE kind = 'reminder' ORDER BY id DESC LIMIT 20"):
    entregado = r["delivered_at"].split(" ")[0] if r["delivered_at"] else None
    demora = ""
    if entregado:
        from datetime import datetime
        demora = f"{(datetime.fromisoformat(entregado) - datetime.fromisoformat(r['created_at'])).total_seconds():.0f} s"
    print(f"{r['id']:>6} {r['ref_id'] or '':>5} {loc(r['created_at']):15} {loc(entregado):15} {demora:>7} {r['intentos']:>8} "
          f"{r['acuse'] or ''} {r['ultimo_error'] or ''}")
PY

linea "Buzón pendiente (por tipo)"
docker compose exec -T core python - <<'PY'
import sqlite3
from core import db
c = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
filas = c.execute("SELECT channel, kind, COUNT(*), MIN(created_at), MAX(intentos) FROM outbox WHERE delivered_at IS NULL GROUP BY channel, kind").fetchall()
print("\n".join(f"{ch} · {k}: {n} pendiente(s), el más viejo de {viejo}, hasta {it} intento(s)" for ch, k, n, viejo, it in filas) or "vacío")
PY

linea "Errores de Telegram y del programador (últimas 24 h)"
docker compose logs --since 24h --no-color telegram 2>/dev/null \
  | grep -E "No se pudo entregar|no entregable|sin acuse|Telegram no disponible|HTTP (4|5)[0-9]{2}|429|bloque|blocked|Otro proceso" \
  | tail -40 || echo "(sin errores del canal)"
docker compose logs --since 24h --no-color core 2>/dev/null \
  | grep -E "Vigilante|Error en el programador|Buzón [0-9]+ .*no se pudo entregar" | tail -20 || echo "(sin alertas del vigilante)"
