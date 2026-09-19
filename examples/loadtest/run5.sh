#!/usr/bin/env bash
# Run 5 (docs/site/reference/load-and-scale.md): the API/worker split under load.
# `serve --role api` accepts + enqueues; N `swarmkit worker` processes execute. Sweep N and show
# aggregate throughput scaling past the single-event-loop ceiling Runs 1-4 found. Postgres only.
#
#   SWARMKIT_STORE_URL=postgresql+psycopg://... examples/loadtest/run5.sh <ws> <topo> [lat] [conc] [dur] [workers_csv]
#   SWARMKIT_STORE_URL=... examples/loadtest/run5.sh typical typical 2000 100 20 "1,2,4,8"
#
# The driver STREAMS each run (SSE) rather than polling, so the poller does not mask throughput.
set -u
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
WS="${1:?workspace}"; TOPO="${2:?topology}"; LAT="${3:-2000}"; CONC="${4:-100}"; DUR="${5:-20}"; WORKERS="${6:-1,2,4,8}"
: "${SWARMKIT_STORE_URL:?Run 5 requires Postgres — export SWARMKIT_STORE_URL}"
export SWARMKIT_PROVIDER=mock SWARMKIT_MOCK_LATENCY_MS="$LAT" SWARMKIT_MOCK_LATENCY_JITTER_MS=$((LAT/4))
# Per-worker pool sized down so N workers stay within Postgres max_connections (worker-execution.md
# connection budget). N x (pool+overflow+checkpointer) must fit; small pools suffice because the
# mock's latency is model time, not DB time — connections are held only briefly during writes.
export SWARMKIT_STORE_POOL_SIZE="${SWARMKIT_STORE_POOL_SIZE:-5}" SWARMKIT_STORE_MAX_OVERFLOW="${SWARMKIT_STORE_MAX_OVERFLOW:-5}"
[ "${DELEG:-0}" = "1" ] && export SWARMKIT_MOCK_DELEGATE=1

WSPATH="examples/loadtest/workspaces/$WS"
kill_port() { local p; p=$(ss -ltnp 2>/dev/null | grep ':8125 ' | sed -n 's/.*pid=\([0-9]*\).*/\1/p'); [ -n "$p" ] && kill "$p" 2>/dev/null; }
kill_port; pkill -f "swarmkit worker $WSPATH" 2>/dev/null; sleep 1

# One API tier for the whole sweep: it only accepts + enqueues + streams, never executes.
nohup uv run swarmkit serve "$WSPATH" --role api --port 8125 --host 127.0.0.1 >/tmp/lt5-serve.log 2>&1 &
for _ in $(seq 1 60); do curl -s http://127.0.0.1:8125/health >/dev/null 2>&1 && break; sleep 1; done
API_PID=$(ss -ltnp 2>/dev/null | grep ':8125 ' | sed -n 's/.*pid=\([0-9]*\).*/\1/p')
echo "api pid=$API_PID ws=$WS topo=$TOPO latency=${LAT}ms conc=$CONC pool=$SWARMKIT_STORE_POOL_SIZE store=postgres"
echo "workers  throughput_rps  run_p50  run_p95  run_p99  completed  429  err"

for N in ${WORKERS//,/ }; do
  pkill -f "swarmkit worker $WSPATH" 2>/dev/null; sleep 1
  WPIDS=()
  for _ in $(seq 1 "$N"); do
    nohup uv run swarmkit worker "$WSPATH" --poll-seconds 0.1 >>/tmp/lt5-worker.log 2>&1 &
    WPIDS+=($!)
  done
  sleep 3  # let workers boot + open their pools before the ramp
  OUT="/tmp/lt5-n${N}.json"
  uv run python examples/loadtest/driver/loadtest.py ramp \
    --topology "$TOPO" --levels "$CONC" --duration "$DUR" --stream --pid "$API_PID" --out "$OUT" >/dev/null 2>&1
  python3 - "$N" "$OUT" <<'PY'
import json, sys
n, out = sys.argv[1], sys.argv[2]
r = json.load(open(out))[0]
print(f"{n:<8} {r['throughput_rps']:<15} {r['run_p50_ms']:<8} {r['run_p95_ms']:<8} "
      f"{r['run_p99_ms']:<8} {r['completed']:<10} {r['busy_429']:<4} {r['errors']}")
PY
  pkill -f "swarmkit worker $WSPATH" 2>/dev/null
done

kill_port
echo "done — per-N JSON in /tmp/lt5-n*.json, serve log /tmp/lt5-serve.log"
