#!/usr/bin/env bash
# One-shot screenshot capture: boots mock-runtime + Next dev, runs the Playwright script,
# tears everything down. Intended for PR authors — commit the resulting PNGs.
#
# Usage: ./scripts/capture-screenshots.sh

set -euo pipefail
cd "$(dirname "$0")/.."

export SESSION_SECRET="${SESSION_SECRET:-$(openssl rand -hex 32)}"
export DOTS_OWNER_PASSWORD="${DOTS_OWNER_PASSWORD:-change-me}"
if [[ -z "${DOTS_OWNER_PASSWORD_HASH:-}" ]]; then
	# hash-password.mjs reads from stdin and prints a quoted + $-escaped hash intended for .env;
	# unquote and un-escape for direct env use here.
	raw="$(printf '%s' "$DOTS_OWNER_PASSWORD" | node scripts/hash-password.mjs)"
	raw="${raw%\"}"
	raw="${raw#\"}"
	export DOTS_OWNER_PASSWORD_HASH="${raw//\\$/$}"
fi
export DOTS_OWNER_USERNAME="${DOTS_OWNER_USERNAME:-owner}"

cleanup() {
	kill "${MOCK_PID:-0}" "${DEV_PID:-0}" 2>/dev/null || true
	wait 2>/dev/null || true
}
trap cleanup EXIT

pnpm mock-runtime >/tmp/dots-mock.log 2>&1 &
MOCK_PID=$!
SWARMKIT_URL=http://127.0.0.1:4100 pnpm dev >/tmp/dots-dev.log 2>&1 &
DEV_PID=$!

# Wait for next dev to be ready (mock-runtime is simpler and comes up instantly).
for i in {1..60}; do
	if curl -fs http://127.0.0.1:3500/login >/dev/null 2>&1; then break; fi
	sleep 1
done

node scripts/screenshots.mjs
