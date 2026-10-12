#!/usr/bin/env bash
# VK Realty client deploy — Layer 2 only. No infrastructure changes required.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    echo "Creating .env from .env.example — edit LIVEKIT_* and API keys before production use."
    cp .env.example .env
  else
    echo "Missing .env — copy .env.example to .env and configure." >&2
    exit 1
  fi
fi

mkdir -p logs data/agent_logs data/greeting_pcm_cache configs

echo "Building and starting VK Realty client..."
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi

$DC build --pull
$DC up -d

echo ""
echo "VK Realty client is starting."
echo "  Dashboard: http://localhost:${CLIENT_HTTP_PORT:-8001}${CLIENT_BASE_PATH:-/realestate}/dashboard"
echo "  Health:    http://localhost:${CLIENT_HTTP_PORT:-8001}/health"
echo ""
$DC ps