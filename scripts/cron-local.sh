#!/usr/bin/env bash
# Alternativa a GitHub Actions: correr la recolección desde tu propia máquina.
#
# Instalación (una sola vez):
#   chmod +x scripts/cron-local.sh
#   crontab -e
#   # y agregá esta línea (lunes 5 a.m.):
#   0 5 * * 1 /ruta/a/pulserival/scripts/cron-local.sh >> /ruta/a/pulserival/salida/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pulserival.cli ciclo
echo "[$(date)] corrida terminada. Borradores en borradores/"
