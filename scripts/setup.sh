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
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \\n+    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
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
if [[ "$(sudo ufw status | head -n1)" == "Status: inactive" ]]; then
  echo "y" | sudo ufw enable || true
fi

mkdir -p data/wg-easy data/bot

# Generate bcrypt hash for wg-easy if needed
if [[ -z "${WG_EASY_PASSWORD_HASH:-}" && -n "${WG_EASY_PASSWORD:-}" ]]; then
  yellow "Generating PASSWORD_HASH for wg-easy..."
  HASH=$(docker run --rm weejewel/wg-easy:latest wgpw "$WG_EASY_PASSWORD" | tr -d '\r')
  if [[ -n "$HASH" ]]; then
    # Replace or append WG_EASY_PASSWORD_HASH in .env
    if grep -q '^WG_EASY_PASSWORD_HASH=' .env; then
      sed -i "s|^WG_EASY_PASSWORD_HASH=.*$|WG_EASY_PASSWORD_HASH=$HASH|" .env
    else
      echo "WG_EASY_PASSWORD_HASH=$HASH" >> .env
    fi
    green "PASSWORD_HASH generated."
  else
    red "Failed to generate PASSWORD_HASH."
  fi
fi

yellow "Starting services with Docker Compose..."
docker compose pull | cat || true
docker compose up -d | cat

green "All set. If this is your first run, you may need to re-login for Docker group to take effect."
green "wg-easy UI: http://$(hostname -I | awk '{print $1}'):51821"


