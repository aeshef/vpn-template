#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy env.example to .env and fill it first." >&2
  exit 1
fi

source .env

PORT=${XRAY_PORT:-443}
UUID=${XRAY_UUID:-}
SNI=${REALITY_SNI:-}
SID=${REALITY_SHORT_ID:-}
PUB=${REALITY_PUBLIC_KEY:-}
HOST=${WG_HOST:-}

if [[ -z "${UUID}" || -z "${SNI}" || -z "${SID}" || -z "${PUB}" || -z "${HOST}" ]]; then
  echo "Some required variables are missing. Ensure XRAY_UUID, REALITY_SNI, REALITY_SHORT_ID, REALITY_PUBLIC_KEY, WG_HOST are set in .env" >&2
  exit 1
fi

# Build VLESS Reality URL
# Format: vless://UUID@HOST:PORT?type=tcp&security=reality&pbk=PUB&sid=SID&sni=SNI&flow=xtls-rprx-vision#label
QUERY="type=tcp&security=reality&pbk=${PUB}&sid=${SID}&sni=${SNI}&flow=xtls-rprx-vision"
URL="vless://${UUID}@${HOST}:${PORT}?${QUERY}#vless-reality"

echo "$URL"


