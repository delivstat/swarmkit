#!/bin/sh
# Minder entrypoint — starts all services in a single container.
# 1. Onboarding webapp (port 80) — always runs, first thing user sees
# 2. SwarmKit serve (port 8321) — always runs, provides MCP tools
# 3. Telegram bot — only if MINDER_TELEGRAM_TOKEN is set

set -e

echo "[minder] Starting SwarmKit serve on :8321..."
swarmkit serve /app/workspace --port 8321 --host 127.0.0.1 &
SWARMKIT_PID=$!

# Give SwarmKit a moment to bind before starting dependents
sleep 2

echo "[minder] Starting onboarding webapp on :${MINDER_WEBAPP_PORT:-80}..."
python3 /app/webapp/app.py &

if [ -n "$MINDER_TELEGRAM_TOKEN" ]; then
  echo "[minder] Starting Telegram bot..."
  python3 /app/bot.py &
else
  echo "[minder] MINDER_TELEGRAM_TOKEN not set — configure via http://minder.local"
fi

echo "[minder] All services started."
wait
