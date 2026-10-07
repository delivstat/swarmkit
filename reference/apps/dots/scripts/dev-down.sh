#!/usr/bin/env bash
# Stop any dev-up.sh processes still running (swarmkit serve on $SWARMKIT_PORT and
# the next dev server on $DOTS_PORT). Safe to run even if nothing is running.

set -euo pipefail

: "${SWARMKIT_PORT:=8099}"
: "${DOTS_PORT:=3509}"

stop() {
	local pattern="$1" label="$2"
	local pids
	pids=$(pgrep -f "$pattern" 2>/dev/null || true)
	if [[ -z "$pids" ]]; then
		echo "$label: nothing to stop"
		return
	fi
	echo "$label: killing $pids"
	kill $pids 2>/dev/null || true
	sleep 1
	pids=$(pgrep -f "$pattern" 2>/dev/null || true)
	[[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
}

stop "swarmkit serve.*--port $SWARMKIT_PORT" "swarmkit serve"
stop "next dev.*-p $DOTS_PORT"               "dots-app dev"
stop "cloudflared tunnel.*127.0.0.1:$DOTS_PORT"     "cloudflared app"
stop "cloudflared tunnel.*127.0.0.1:$SWARMKIT_PORT" "cloudflared serve"
