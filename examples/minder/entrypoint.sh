#!/bin/sh
# Minder entrypoint — starts all services in a single container, each under a
# restart loop so a crash of any one auto-recovers (none can stay dead).
# 1. SwarmKit serve (port 8321) — MCP tools / topologies
# 2. Onboarding webapp + ops API (port 80)
# 3. Telegram bot — only if MINDER_TELEGRAM_TOKEN is set
#
# `until <svc>; do ...; done` re-runs the service whenever it exits non-zero
# (a crash) and stops only on a clean exit. The container (PID 1 = this script)
# is the supervisor; a separate watchdog also probes liveness (see
# webapp health monitor) and the SwarmKit serve port before reporting healthy.

# supervise NAME CMD... — run CMD forever, restarting 5s after any crash.
supervise() {
  name="$1"
  shift
  (
    until "$@"; do
      echo "[minder] $name exited (code $?), restarting in 5s..." >&2
      sleep 5
    done
  ) &
}

echo "[minder] Starting SwarmKit serve on :8321..."
supervise swarmkit-serve swarmkit serve /app/workspace --port 8321 --host 127.0.0.1

# Give SwarmKit a moment to bind before starting dependents
sleep 2

echo "[minder] Starting onboarding webapp on :${MINDER_WEBAPP_PORT:-80}..."
supervise webapp python3 /app/webapp/app.py

if [ -n "$MINDER_TELEGRAM_TOKEN" ]; then
  echo "[minder] Starting Telegram bot..."
  supervise telegram-bot python3 /app/bot.py
else
  echo "[minder] MINDER_TELEGRAM_TOKEN not set — configure via http://minder.local"
fi

echo "[minder] All services started (supervised)."
wait
