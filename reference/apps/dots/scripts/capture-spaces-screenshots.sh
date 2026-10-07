#!/usr/bin/env bash
# Screenshot capture for the Spaces slice. Needs a REAL swarmkit serve running at
# $SWARMKIT_URL (default http://127.0.0.1:8099) — see docs/spaces.md for setup. Boots
# only the dots-app dev server + runs the Playwright script against the real backend.
#
# Usage:
#   OPENROUTER_API_KEY=sk-or-... ./scripts/capture-spaces-screenshots.sh

set -euo pipefail
cd "$(dirname "$0")/.."

: "${SWARMKIT_URL:=http://127.0.0.1:8099}"
export SWARMKIT_URL
export SESSION_SECRET="${SESSION_SECRET:-$(openssl rand -hex 32)}"
export DOTS_OWNER_PASSWORD="${DOTS_OWNER_PASSWORD:-change-me}"
if [[ -z "${DOTS_OWNER_PASSWORD_HASH:-}" ]]; then
	raw="$(printf '%s' "$DOTS_OWNER_PASSWORD" | node scripts/hash-password.mjs)"
	raw="${raw%\"}"
	raw="${raw#\"}"
	export DOTS_OWNER_PASSWORD_HASH="${raw//\\$/$}"
fi
export DOTS_OWNER_USERNAME="${DOTS_OWNER_USERNAME:-owner}"

# Reset spaces.local.json so the demo starts from a clean 'no Spaces yet' state.
echo "[]" > lib/spaces.local.json

cleanup() {
	kill "${DEV_PID:-0}" 2>/dev/null || true
	wait 2>/dev/null || true
}
trap cleanup EXIT

pnpm exec next dev -p 3509 >/tmp/dots-spaces-dev.log 2>&1 &
DEV_PID=$!

for i in {1..60}; do
	if curl -fs http://127.0.0.1:3509/login >/dev/null 2>&1; then break; fi
	sleep 1
done

APP_URL=http://127.0.0.1:3509 node scripts/screenshot-spaces.mjs
