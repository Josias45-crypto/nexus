#!/usr/bin/env bash
# Trae a la laptop los respaldos de Isabelle que están en el VPS (docs/DESPLIEGUE.md, paso 9).
# Se ejecuta EN LA LAPTOP. Solo lee del servidor: nunca borra nada allá.
#
#   scripts/respaldo_remoto.sh [usuario@servidor] [carpeta_local]
#   ISABELLE_VPS=isabelle@mi-vps scripts/respaldo_remoto.sh
#
# Copia ~/isabelle/backups del servidor (copias verificadas de la base y espejo de los originales)
# a la carpeta local (por defecto ~/respaldos-isabelle). rsync solo trae lo nuevo o cambiado.
# Para hacerlo cada día a las 13:00 (crontab -e en la laptop):
#   0 13 * * * /ruta/al/repo/scripts/respaldo_remoto.sh isabelle@mi-vps >> ~/respaldos-isabelle.log 2>&1
set -euo pipefail

SERVIDOR="${1:-${ISABELLE_VPS:-}}"
DESTINO="${2:-$HOME/respaldos-isabelle}"
REMOTO="${ISABELLE_DIR_REMOTO:-isabelle/backups}"  # relativo al home del usuario del servidor

if [[ -z "$SERVIDOR" ]]; then
  echo "Uso: $0 usuario@servidor [carpeta_local]  (o define ISABELLE_VPS)" >&2
  exit 2
fi
command -v rsync >/dev/null || { echo "Falta rsync: sudo apt install rsync" >&2; exit 2; }

mkdir -p "$DESTINO"
echo "$(date '+%F %T') · trayendo $SERVIDOR:$REMOTO → $DESTINO"
# -a conserva fechas y permisos; sin --delete: lo que se rote en el servidor queda en la laptop
rsync -a --partial --human-readable --stats -e "ssh -o BatchMode=yes" \
  "$SERVIDOR:$REMOTO/" "$DESTINO/" | grep -E "Number of regular files transferred|Total transferred file size" || true

ultima=$(ls -1t "$DESTINO"/db/*.db 2>/dev/null | head -1 || true)
if [[ -z "$ultima" ]]; then
  echo "Aviso: no llegó ninguna copia de la base (.db). ¿El núcleo ya hizo su primer respaldo?" >&2
  exit 1
fi
edad_h=$(( ( $(date +%s) - $(stat -c %Y "$ultima") ) / 3600 ))
echo "Última copia: $(basename "$ultima") (hace ${edad_h} h)"
if (( edad_h > 48 )); then
  echo "Aviso: la última copia tiene más de 48 h; revisa GET /backup en el servidor." >&2
  exit 1
fi
