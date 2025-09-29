#!/usr/bin/env bash
set -euo pipefail

# Colors
red() { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  red "Missing .env. Copy env.example to .env and fill it first."
  exit 1
fi

source .env || true

yellow "Updating apt index..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw git

if ! command -v docker >/dev/null 2>&1; then
  yellow "Installing Docker..."
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable
EOF
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER" || true
fi

yellow "Ensuring sysctl for WireGuard..."
sudo bash -c 'cat >/etc/sysctl.d/99-wireguard-forward.conf <<EOF
net.ipv4.ip_forward=1
net.ipv4.conf.all.src_valid_mark=1
EOF'
sudo sysctl --system | cat

yellow "Configuring UFW..."
sudo ufw allow 22/tcp || true
sudo ufw allow 51821/tcp || true
sudo ufw allow 51820/udp || true
if [[ "${XRAY_ENABLED:-false}" == "true" ]]; then
  sudo ufw allow ${XRAY_PORT:-443}/tcp || true
  sudo ufw allow ${XRAY_PORT:-443}/udp || true
fi
if [[ "$(sudo ufw status | head -n1)" == "Status: inactive" ]]; then
  echo "y" | sudo ufw enable || true
fi

mkdir -p data/wg-easy data/bot data/xray

# Try to generate bcrypt hash for wg-easy if possible; fall back to PASSWORD env
if [[ -z "${WG_EASY_PASSWORD_HASH:-}" && -n "${WG_EASY_PASSWORD:-}" ]]; then
  yellow "Attempting to generate PASSWORD_HASH for wg-easy (optional)..."
  set +e
  HASH=$(docker run --rm weejewel/wg-easy:latest node -e 'const bcrypt=require("bcryptjs");console.log(bcrypt.hashSync(process.argv[1],10));' "$WG_EASY_PASSWORD" 2>/dev/null)
  RC=$?
  set -e
  if [[ $RC -eq 0 && -n "$HASH" ]]; then
    if grep -q '^WG_EASY_PASSWORD_HASH=' .env; then
      sed -i "s|^WG_EASY_PASSWORD_HASH=.*$|WG_EASY_PASSWORD_HASH=$HASH|" .env
    else
      echo "WG_EASY_PASSWORD_HASH=$HASH" >> .env
    fi
    green "PASSWORD_HASH generated."
  else
    yellow "Could not generate bcrypt hash; will use plain PASSWORD env."
  fi
fi

yellow "Starting services with Docker Compose..."
docker compose pull | cat || true
docker compose up -d | cat

# Configure Xray (Reality) if enabled
if [[ "${XRAY_ENABLED:-false}" == "true" ]]; then
  yellow "Configuring Xray (Reality)..."
  # Generate keys if missing
  if [[ -z "${REALITY_PRIVATE_KEY:-}" || -z "${REALITY_PUBLIC_KEY:-}" ]]; then
    KEYS=$(docker run --rm teddysun/xray:latest xray x25519 | tr -d '\r')
    PRIV=$(echo "$KEYS" | awk '/Private key/{print $3}')
    PUB=$(echo "$KEYS" | awk '/Public key/{print $3}')
    if [[ -n "$PRIV" && -n "$PUB" ]]; then
      grep -q '^REALITY_PRIVATE_KEY=' .env && sed -i "s|^REALITY_PRIVATE_KEY=.*$|REALITY_PRIVATE_KEY=$PRIV|" .env || echo "REALITY_PRIVATE_KEY=$PRIV" >> .env
      grep -q '^REALITY_PUBLIC_KEY=' .env && sed -i "s|^REALITY_PUBLIC_KEY=.*$|REALITY_PUBLIC_KEY=$PUB|" .env || echo "REALITY_PUBLIC_KEY=$PUB" >> .env
      export REALITY_PRIVATE_KEY=$PRIV REALITY_PUBLIC_KEY=$PUB
    fi
  fi
  if [[ -z "${REALITY_SHORT_ID:-}" ]]; then
    SID=$(openssl rand -hex 4)
    grep -q '^REALITY_SHORT_ID=' .env && sed -i "s|^REALITY_SHORT_ID=.*$|REALITY_SHORT_ID=$SID|" .env || echo "REALITY_SHORT_ID=$SID" >> .env
    export REALITY_SHORT_ID=$SID
  fi
  if [[ -z "${XRAY_UUID:-}" ]]; then
    UUID=$(cat /proc/sys/kernel/random/uuid)
    grep -q '^XRAY_UUID=' .env && sed -i "s|^XRAY_UUID=.*$|XRAY_UUID=$UUID|" .env || echo "XRAY_UUID=$UUID" >> .env
    export XRAY_UUID=$UUID
  fi
  DEST=${REALITY_DEST:-www.cloudflare.com:443}
  SNI=${REALITY_SNI:-www.cloudflare.com}
  PORT=${XRAY_PORT:-443}
  cat > data/xray/config.json <<JSON
{
  "inbounds": [
    {
      "tag": "vless-reality",
      "port": $PORT,
      "protocol": "vless",
      "settings": {
        "clients": [
          { "id": "$XRAY_UUID", "email": "default@local", "flow": "xtls-rprx-vision" }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "$DEST",
          "xver": 0,
          "serverNames": ["$SNI"],
          "privateKey": "$REALITY_PRIVATE_KEY",
          "shortIds": ["$REALITY_SHORT_ID"]
        }
      },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] }
    }
  ],
  "outbounds": [ { "protocol": "freedom" } ]
}
JSON
  docker compose restart xray || true
fi

green "All set. If this is your first run, you may need to re-login for Docker group to take effect."
green "wg-easy UI: http://$(hostname -I | awk '{print $1}'):51821"


