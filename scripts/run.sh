#!/usr/bin/env bash
set -euo pipefail
chmod +x "$0" >/dev/null 2>&1 || true

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

CMD=${1:-help}

case "$CMD" in
  up)
    docker compose up -d ;;
  down)
    docker compose down ;;
  restart)
    docker compose down && docker compose up -d ;;
  logs)
    docker compose logs -f vpn-bot | cat ;;
  pull)
    docker compose pull | cat ;;
  build)
    docker compose build | cat ;;
  ps)
    docker compose ps | cat ;;
  *)
    echo "Usage: $0 {up|down|restart|logs|pull|build|ps}" ;;
esac


