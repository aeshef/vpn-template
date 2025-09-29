#!/usr/bin/env bash
set -euo pipefail
set -x

mkdir -p /app/data
export PYTHONUNBUFFERED=1

python -u /app/app.py || {
  echo "[vpn-bot] app crashed, keeping container for logs" >&2
  sleep 600
}


