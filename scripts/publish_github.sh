#!/usr/bin/env bash
set -euo pipefail

REMOTE_URL=${1:-}
if [[ -z "$REMOTE_URL" ]]; then
  echo "Usage: $0 <REMOTE_URL>"
  echo "Example: $0 https://github.com/aeshef/vpn-template.git"
  exit 1
fi

git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin "$REMOTE_URL" || git remote set-url origin "$REMOTE_URL"
git push -u origin main

echo "Pushed to $REMOTE_URL"


