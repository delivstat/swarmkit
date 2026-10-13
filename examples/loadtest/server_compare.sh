#!/usr/bin/env bash
# Run 7 (docs/site/reference/load-and-scale.md): the same app under uvicorn vs Granian.
#
# Serves examples/loadtest/asgi_app.py (create_app, exactly as `swarmkit serve` builds it) under
# each server with ONE worker — `serve` is one process, one loop — and storms POST /run past a
# bounded queue so both the accept path and the 429 path are measured. Two modes:
#
#   api  `serve --role api`: accept + enqueue into Postgres, no execution (Postgres required)
#   all  all-in-one: runs execute on the server's own loop (the slow-429 case, Result 4)
#
#   SWARMKIT_STORE_URL=postgresql+psycopg://... examples/loadtest/server_compare.sh api 5000 200
#   examples/loadtest/server_compare.sh all 2000 200
set -u
cd "$(dirname "$0")/../.."
MODE="${1:-api}"; N="${2:-5000}"; CONNS="${3:-200}"; REPEATS="${REPEATS:-3}"
PORT=8125
export LT_WORKSPACE="examples/loadtest/workspaces/tiny" LT_ROLE="$MODE" LT_QUEUE_DEPTH="${LT_QUEUE_DEPTH:-1000}"
export SWARMKIT_PROVIDER=mock SWARMKIT_MOCK_LATENCY_MS="${LAT:-500}" SWARMKIT_MOCK_LATENCY_JITTER_MS=$(( ${LAT:-500} / 4 ))
if [ "$MODE" = "api" ]; then
  : "${SWARMKIT_STORE_URL:?mode api needs Postgres — export SWARMKIT_STORE_URL}"
fi

reset_jobs() {
  [ "$MODE" = "api" ] || return 0
  uv run python - <<'PY'
import os
from sqlalchemy import delete
from swarmkit_runtime.persistence._store import make_engine
from swarmkit_runtime.persistence._tables import jobs
e = make_engine(os.environ["SWARMKIT_STORE_URL"])
with e.begin() as c:
    c.execute(delete(jobs))
e.dispose()
PY
}

stop_port() {
  local p; p=$(ss -ltnp 2>/dev/null | grep ":$PORT " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
  [ -n "$p" ] && kill "$p" 2>/dev/null
  for _ in $(seq 1 20); do ss -ltn | grep -q ":$PORT " || return 0; sleep 0.5; done
}

start() {
  case "$1" in
    uvicorn) nohup uv run uvicorn --factory --app-dir examples/loadtest asgi_app:app \
               --host 127.0.0.1 --port $PORT --log-level warning >/tmp/lt7-uvicorn.log 2>&1 & ;;
    granian) nohup uv run --with granian granian --interface asgi --factory \
               --working-dir examples/loadtest asgi_app:app --host 127.0.0.1 --port $PORT \
               --workers 1 --log-level warning >/tmp/lt7-granian.log 2>&1 & ;;
  esac
  for _ in $(seq 1 90); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "$1 did not come up — see /tmp/lt7-$1.log" >&2; return 1
}

echo "mode=$MODE n=$N connections=$CONNS queue_depth=$LT_QUEUE_DEPTH latency=${LAT:-500}ms repeats=$REPEATS"
stop_port
for server in uvicorn granian; do
  for i in $(seq 1 "$REPEATS"); do
    reset_jobs
    start "$server" || exit 1
    sleep 2
    printf '%-8s #%s  ' "$server" "$i"
    uv run python examples/loadtest/driver/loadtest.py storm --api "http://127.0.0.1:$PORT" \
      --topology tiny --n "$N" --connections "$CONNS" 2>&1 | tail -1
    stop_port
  done
done
reset_jobs
