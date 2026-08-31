#!/usr/bin/env bash
# Start Pot with clean environment (no proxy, uses Gemini directly)
set -euo pipefail
cd ~/apps/imobile
source .env 2>/dev/null || true

# Unset any stale proxy vars that Pot might pick up
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY

echo "Starting Pot (Gemini-only mode)..."
XDG_SESSION_TYPE=x11 DISPLAY=:1 /home/kasm-user/.local/bin/pot_3.0.7_amd64.appimage --no-sandbox