#!/usr/bin/env bash
set -euo pipefail

# Colors
red() { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

WIPE_DATA="false"
NO_START="false"
FORCE="false"

usage() {
  cat <<USAGE
Usage: $0 [--wipe-data] [--no-start] [--force]

  --wipe-data  Remove ./data/wg-easy, ./data/xray, and bot DB before start
  --no-start   Only diagnose and clean, do not run setup/start
  --force      Proceed without any confirmations
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --wipe-data) WIPE_DATA="true" ;;
    --no-start)  NO_START="true" ;;
    --force)     FORCE="true" ;;
    -h|--help)   usage; exit 0 ;;
    *) red "Unknown arg: $arg"; usage; exit 1 ;;
  esac
done

# Try to load env to know which ports/features are enabled
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env || true
fi

AWG_ENABLED=${AWG_ENABLED:-false}
AWG_PORT=${AWG_PORT:-443}
XRAY_ENABLED=${XRAY_ENABLED:-false}
XRAY_PORT=${XRAY_PORT:-443}

echo
yellow "[1/6] Diagnose current state"
echo "- Docker present: $(command -v docker >/dev/null 2>&1 && echo yes || echo no)"
echo "- Compose present: $(command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && echo yes || echo no)"
echo "- Existing containers:"
docker ps -a --format 'table {{.Names}}\t{{.Status}}' | sed '1!b;1p;$!{/^$/d}' || true
echo
echo "- WireGuard interfaces:"
ip link 2>/dev/null | grep -E 'wg[0-9]?' || echo "(none)"
echo
echo "- iptables NAT redirect 443->51820 (legacy):"
iptables -t nat -S PREROUTING 2>/dev/null | grep -- '--dport 443' | grep REDIRECT || echo "(none)"
echo
echo "- UFW status:"
sudo ufw status || true
echo
echo "- Listening ports (tcp/udp 22/443/51820/51821):"
ss -lnptu | grep -E ':(22|443|51820|51821) ' || true

echo
yellow "[2/6] Stop and remove old containers if present"
stop_and_rm() {
  local name="$1"
  if docker ps -a --format '{{.Names}}' | grep -wq "$name"; then
    yellow "- Stopping $name"
    docker stop "$name" >/dev/null 2>&1 || true
    yellow "- Removing $name"
    docker rm -f "$name" >/dev/null 2>&1 || true
  fi
}
stop_and_rm wg-easy
stop_and_rm xray
stop_and_rm vpn-bot

echo
yellow "[3/6] Remove legacy iptables redirect 443/udp -> 51820/udp"
iptables -t nat -C PREROUTING -p udp --dport 443 -j REDIRECT --to-ports 51820 2>/dev/null && \
  iptables -t nat -D PREROUTING -p udp --dport 443 -j REDIRECT --to-ports 51820 || true

echo
yellow "[4/6] Bring down stale wg interfaces"
set +e
if command -v wg-quick >/dev/null 2>&1; then
  sudo wg-quick down wg0 2>/dev/null
fi
sudo ip link del dev wg0 2>/dev/null
set -e

echo
yellow "[5/6] Ensure UFW rules"
sudo ufw allow 22/tcp || true
sudo ufw allow 51821/tcp || true
sudo ufw allow 51820/udp || true
if [[ "${AWG_ENABLED}" == "true" ]]; then
  sudo ufw allow ${AWG_PORT}/udp || true
fi
if [[ "${XRAY_ENABLED}" == "true" ]]; then
  sudo ufw allow ${XRAY_PORT}/tcp || true
  sudo ufw allow ${XRAY_PORT}/udp || true
fi
if [[ "$(sudo ufw status | head -n1)" == "Status: inactive" ]]; then
  echo "y" | sudo ufw enable || true
fi

if [[ "$WIPE_DATA" == "true" ]]; then
  echo
  yellow "[5b] Wiping data directory (wg-easy, xray, bot DB)"
  rm -rf ./data/wg-easy || true
  rm -rf ./data/xray || true
  rm -f  ./data/bot/metrics.sqlite || true
fi

if [[ "$NO_START" == "true" ]]; then
  green "Clean done. Skipping start per --no-start."
  exit 0
fi

echo
yellow "[6/6] Run setup and start services"
sudo bash ./scripts/setup.sh

echo
green "Doctor done. Current compose status:"
docker compose ps | cat || true

echo
green "Listening ports after start:"
ss -lnptu | grep -E ':(22|443|51820|51821) ' || true


