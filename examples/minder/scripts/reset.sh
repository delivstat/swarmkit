#!/usr/bin/env bash
# Reset Minder to a FRESH INSTALL — wipes all runtime state and restarts.
#
# Removes every Minder docker volume and the host backups, then brings the
# stack back up clean so you can run onboarding from scratch. Your .env secrets
# (Telegram token, camera/OpenRouter/HA credentials) are kept.
#
#   ./scripts/reset.sh          # prompts for confirmation, then resets + starts
#   ./scripts/reset.sh --yes    # no prompt (for scripted test loops)
#   ./scripts/reset.sh --no-up  # wipe only, don't restart
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIRM=1 UP=1
for a in "$@"; do
  case "$a" in
    --yes|-y) CONFIRM=0 ;;
    --no-up)  UP=0 ;;
  esac
done

if [ "$CONFIRM" = 1 ]; then
  cat <<'MSG'
This WIPES all Minder state for a fresh install:
  - minder-data    (discovered cameras, rules, HA token, Telegram group, events)
  - ha-config      (Home Assistant account, integrations, automations)
  - frigate-config (generated camera config) + frigate-media (recordings)
  - ./backups      (recovery snapshots)
Your .env secrets are kept.
MSG
  printf "Type 'reset' to confirm: "
  read -r ans
  [ "$ans" = "reset" ] || { echo "aborted"; exit 1; }
fi

echo "Stopping containers + removing volumes..."
docker compose down -v

echo "Clearing host backups (container-owned, via throwaway container)..."
docker run --rm -v "$PWD/backups:/b" busybox sh -c "rm -rf /b/* /b/.[!.]* 2>/dev/null || true"

if [ "$UP" = 1 ]; then
  echo "Starting fresh..."
  docker compose up -d
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "Fresh install starting. Onboard at: http://${ip:-<box-ip>}/"
  echo "(Home Assistant first-boot setup takes a few minutes.)"
else
  echo "Wiped. Run 'docker compose up -d' to start fresh."
fi
