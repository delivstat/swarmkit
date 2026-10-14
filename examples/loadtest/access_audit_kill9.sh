#!/usr/bin/env bash
# What a kill -9 costs the serve access audit, per --access-audit mode (api-tier-submit-path.md).
#
# Serves a scratch copy of the tiny workspace (SQLite, mock provider), sends N POST /run, kills the
# process with SIGKILL straight after the last response, and counts the serve_access rows on disk.
#   sync     every acknowledged call has its row — written before the response
#   batched  the rows of the last ~250 ms are lost, and not recovered on restart
#
#   examples/loadtest/access_audit_kill9.sh            # both modes, 20 calls
#   examples/loadtest/access_audit_kill9.sh sync 50
set -u
cd "$(dirname "$0")/../.."
MODES="${1:-sync batched}"; N="${2:-20}"; PORT=8131
WS="$(mktemp -d)/ws"; cp -r examples/loadtest/workspaces/tiny "$WS"
trap 'rm -rf "$(dirname "$WS")"' EXIT

for mode in $MODES; do
  rm -rf "$WS/.swarmkit"
  SWARMKIT_PROVIDER=mock SWARMKIT_MOCK_LATENCY_MS=2000 uv run swarmkit serve "$WS" --host 127.0.0.1 \
    --port $PORT --access-audit "$mode" >"$WS.serve.log" 2>&1 &
  for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
  pid=$(ss -ltnp | grep ":$PORT " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
  ok=0
  for _ in $(seq 1 "$N"); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/run/tiny" \
      -H 'content-type: application/json' -d '{"input":"x"}')
    [ "$code" = 200 ] && ok=$((ok + 1))
  done
  kill -9 "$pid"; sleep 1
  rows=$(uv run python -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute(
    \"select count(*) from serve_access where path='/run/tiny'\").fetchone()[0])" "$WS/.swarmkit/store.sqlite")
  echo "--access-audit $mode: acknowledged=$ok  access rows on disk after kill -9=$rows"
done
