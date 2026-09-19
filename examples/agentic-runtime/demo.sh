#!/usr/bin/env bash
# A ~60-second terminal demo of the SwarmKit runtime around a single model call.
# Runs entirely on the built-in mock provider — no API keys, no network, fully reproducible.
#
#   bash examples/agentic-runtime/demo.sh
#
# Record it as an asciinema cast (then `asciinema upload demo.cast`):
#   uvx asciinema rec -c "bash examples/agentic-runtime/demo.sh" demo.cast
set -euo pipefail

WS="examples/agentic-runtime"
export SWARMKIT_PROVIDER=mock SWARMKIT_MODEL=mock
PAUSE="${DEMO_PAUSE:-1.4}"

say()  { printf '\n\033[1;36m# %s\033[0m\n' "$1"; sleep "$PAUSE"; }
run()  { printf '\033[1;32m$ %s\033[0m\n' "$*"; sleep 0.6; eval "$*"; sleep "$PAUSE"; }

clear || true
say "SwarmKit is an agentic runtime. An agent is data — this file is the whole agent:"
run "sed -n '7,20p' $WS/topologies/assistant.yaml"

say "The runtime — not your app — owns the call to the model. Run it (on the mock provider):"
run "uv run swarmkit run $WS assistant -i 'What is topology-as-data in one line?'"

RUN_ID="$(uv run swarmkit trace -w "$WS" -n 1 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{3}' | head -1)"

say "Every model call is recorded. The runtime accounts for the whole call graph, tokens and cost:"
run "uv run swarmkit trace $RUN_ID -w $WS"

say "And the same run is an append-only audit event — a compliance-ready report, not a log line:"
run "uv run swarmkit logs $WS -n 1 --format markdown"

say "One agent or a swarm of them: the runtime handles the model-call loop the same way."
sleep 1
