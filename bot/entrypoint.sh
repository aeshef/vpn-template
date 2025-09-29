#!/usr/bin/env bash
set -euo pipefail
set -x

mkdir -p /app/data
export PYTHONUNBUFFERED=1

echo "[vpn-bot] starting loop"
while true; do
  echo "[vpn-bot] launching app.py"
  python -u /app/app.py || echo "[vpn-bot] app exited with non-zero"
  echo "[vpn-bot] app finished (exit code $?), restart in 5s"
  sleep 5
done


