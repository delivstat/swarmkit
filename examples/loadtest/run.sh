#!/usr/bin/env bash
# Serve one loadtest workspace with a latency mock and run a concurrency ramp against it.
# See docs/site/reference/load-and-scale.md. Deterministic, free — no real model, no network egress.
#
#   examples/loadtest/run.sh <workspace> <topology> [latency_ms] [levels] [duration] [delegate]
#   examples/loadtest/run.sh tiny    tiny    2000 "5,10,25,50,100" 20
#   examples/loadtest/run.sh typical typical 2000 "5,25,100" 20 1        # fan-out (delegation on)
#
# Postgres (recommended for realistic write load): export SWARMKIT_STORE_URL before running.
# Otherwise it uses the workspace-local SQLite.
set -u
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
WS="${1:?workspace}"; TOPO="${2:?topology}"; LAT="${3:-2000}"; LEVELS="${4:-5,10,25,50,100}"; DUR="${5:-20}"; DELEG="${6:-0}"
export SWARMKIT_PROVIDER=mock SWARMKIT_MOCK_LATENCY_MS="$LAT" SWARMKIT_MOCK_LATENCY_JITTER_MS=$((LAT/4))
[ "$DELEG" = "1" ] && export SWARMKIT_MOCK_DELEGATE=1
rm -rf "examples/loadtest/workspaces/$WS/.swarmkit"
P=$(ss -ltnp | grep ':8125 ' | sed -n 's/.*pid=\([0-9]*\).*/\1/p'); [ -n "$P" ] && kill "$P" 2>/dev/null; sleep 1
nohup uv run swarmkit serve "examples/loadtest/workspaces/$WS" --port 8125 --host 127.0.0.1 >/tmp/lt-serve.log 2>&1 &
for _ in $(seq 1 60); do curl -s http://127.0.0.1:8125/health >/dev/null 2>&1 && break; sleep 1; done
PID=$(ss -ltnp | grep ':8125 ' | sed -n 's/.*pid=\([0-9]*\).*/\1/p')
echo "serve pid=$PID ws=$WS topo=$TOPO latency=${LAT}ms delegate=$DELEG store=${SWARMKIT_STORE_URL:-sqlite}"
uv run python examples/loadtest/driver/loadtest.py ramp --topology "$TOPO" --levels "$LEVELS" --duration "$DUR" --pid "$PID"
kill "$PID" 2>/dev/null
