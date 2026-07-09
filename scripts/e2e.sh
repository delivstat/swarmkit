#!/usr/bin/env bash
# Run the control-plane OIDC-login browser end-to-end test (Playwright + Chromium).
#
# Removed from CI (it downloads Chromium + drives a real browser); run it locally when you touch the
# panel's auth or the fleet UI. The test is self-contained — playwright.config.ts boots a fake OIDC
# IdP, an OIDC-enabled panel, and the UI, then drives the PKCE login flow and asserts the panel
# accepts the issued token.
#
#   ./scripts/e2e.sh            # install deps + browser, then run
#   just e2e                    # same, via the task runner
#   ./scripts/e2e.sh --headed   # extra args pass through to `playwright test`
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

# The test spins up its own IdP (:8402), panel (:8819), and UI (:3000). If something is already on
# those ports — most likely a running dev fleet UI on :3000 — Playwright would reuse it (pointed at
# the wrong panel) and the login assertions fail confusingly. Fail fast with a clear message instead.
for port in 3000 8402 8819; do
  if ss -tlnp 2>/dev/null | grep -q ":$port "; then
    echo "✗ port $port is in use — the e2e needs it free."
    echo "  Stop whatever's on it first (e.g. the dev fleet UI: ~/fleet-demo/stop.sh), then re-run."
    exit 1
  fi
done

echo "→ deps (uv + pnpm)"
uv sync --all-packages --group dev
pnpm install --frozen-lockfile

echo "→ Playwright Chromium"
# Browser binary only (no --with-deps: that needs sudo for apt libraries). If Chromium fails to
# launch on a fresh machine, install the system libs once:
#   sudo pnpm --filter @swarmkit/control-plane-ui exec playwright install-deps chromium
pnpm --filter @swarmkit/control-plane-ui exec playwright install chromium

echo "→ OIDC-login e2e"
pnpm --filter @swarmkit/control-plane-ui exec playwright test "$@"
