#!/usr/bin/env bash
# Boot swarmkit serve + the dots-app dev server for local development, and keep both
# running until Ctrl-C. Writes logs to /tmp/dots-{serve,dev}.log. Pass --tunnel to
# also publish each on the Cloudflare origin named in CLOUDFLARED_DOTS_HOST and
# CLOUDFLARED_SERVE_HOST (requires a `cloudflared` tunnel route for each host).
#
# Env:
#   SWARMKIT_WORKSPACE   workspace dir to serve (default /tmp/spaces-demo)
#   SWARMKIT_PORT        default 8099
#   DOTS_PORT            default 3509
#   CLOUDFLARED_DOTS_HOST   public hostname for the dots app (default dots.delivstat.com)
#   CLOUDFLARED_SERVE_HOST  public hostname for the swarmkit portal (default dots-serve.delivstat.com)
#   OPENROUTER_API_KEY   passed through to swarmkit serve
#
# Usage:
#   ./scripts/dev-up.sh              # local only
#   ./scripts/dev-up.sh --tunnel     # also open two cloudflared tunnels

set -euo pipefail
cd "$(dirname "$0")/.."

: "${SWARMKIT_WORKSPACE:=/tmp/spaces-demo}"
: "${SWARMKIT_PORT:=8099}"
: "${DOTS_PORT:=3509}"
: "${CLOUDFLARED_DOTS_HOST:=dots.delivstat.com}"
: "${CLOUDFLARED_SERVE_HOST:=dots-serve.delivstat.com}"

want_tunnel=0
[[ "${1:-}" == "--tunnel" ]] && want_tunnel=1

if [[ ! -f "$SWARMKIT_WORKSPACE/workspace.yaml" ]]; then
	echo "no workspace.yaml at $SWARMKIT_WORKSPACE — set SWARMKIT_WORKSPACE" >&2
	exit 1
fi

repo_root="$(cd ../.. && pwd)"

pids=()
cleanup() {
	echo
	echo "stopping ${#pids[@]} process(es)..."
	for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
	wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 1. swarmkit serve
cors_args=(
	--cors-origin "http://127.0.0.1:$DOTS_PORT"
	--cors-origin "https://$CLOUDFLARED_DOTS_HOST"
	--cors-origin "https://$CLOUDFLARED_SERVE_HOST"
)
(
	cd "$repo_root"
	uv run swarmkit serve "$SWARMKIT_WORKSPACE" \
		--host 127.0.0.1 --port "$SWARMKIT_PORT" --insecure \
		"${cors_args[@]}"
) >/tmp/dots-serve.log 2>&1 &
pids+=($!)
echo "swarmkit serve   → http://127.0.0.1:$SWARMKIT_PORT   (log: /tmp/dots-serve.log)"

# 2. dots app
SWARMKIT_URL="http://127.0.0.1:$SWARMKIT_PORT" \
	pnpm exec next dev -p "$DOTS_PORT" >/tmp/dots-dev.log 2>&1 &
pids+=($!)
echo "dots-app         → http://127.0.0.1:$DOTS_PORT       (log: /tmp/dots-dev.log)"

# 3. optional cloudflared tunnels
if (( want_tunnel )); then
	if ! command -v cloudflared >/dev/null; then
		echo "cloudflared not installed; --tunnel ignored" >&2
	else
		cloudflared tunnel --url "http://127.0.0.1:$DOTS_PORT" \
			--hostname "$CLOUDFLARED_DOTS_HOST" >/tmp/dots-cf-app.log 2>&1 &
		pids+=($!)
		echo "cloudflared app  → https://$CLOUDFLARED_DOTS_HOST     (log: /tmp/dots-cf-app.log)"

		cloudflared tunnel --url "http://127.0.0.1:$SWARMKIT_PORT" \
			--hostname "$CLOUDFLARED_SERVE_HOST" >/tmp/dots-cf-serve.log 2>&1 &
		pids+=($!)
		echo "cloudflared srv  → https://$CLOUDFLARED_SERVE_HOST    (log: /tmp/dots-cf-serve.log)"
	fi
fi

echo
echo "Login: owner / change-me (or whatever DOTS_OWNER_PASSWORD is set to)"
echo "Press Ctrl-C to stop everything."
wait
