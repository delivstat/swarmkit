#!/usr/bin/env bash
# Demo: one service decides where every store lives, and it says so out loud.
#
# Before this, six components each resolved storage independently and four ignored the config
# entirely. A workspace declaring Postgres ran its sagas, audit trail and governed memory into a
# local SQLite file, `swarmkit validate` passed, and the only symptom was an empty serve UI.
#
#   ./examples/storage-service/demo.sh
#
# Read-only unless you point it at a real Postgres — no database is required to see the resolution.

set -euo pipefail

WS="$(mktemp -d)/ws"
mkdir -p "$WS"
cp -r "$(dirname "$0")/../hello-swarm/workspace/." "$WS/"
# Start from a clean slate, then seed a known row: the split-brain warning in step 5 has to be
# reproducible on someone else's machine, not a reflection of whatever this one happens to hold.
rm -rf "$WS/.swarmkit"
mkdir -p "$WS/.swarmkit"
python3 - "$WS/.swarmkit/store.sqlite" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, topology TEXT, status TEXT, input TEXT, created_at TEXT)")
conn.executemany("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?)",
                 [(f"job-{i}", "hello", "completed", "hi", "2026-08-02") for i in range(7)])
conn.commit()
PY

hr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

hr "1. Default workspace — everything local, and it tells you which rule applied"
uv run swarmkit storage status "$WS"

hr "2. Declare Postgres once, under storage.runtime"
cat >> "$WS/workspace.yaml" <<'YAML'

storage:
  runtime:
    backend: postgres
    url: ${SWARMKIT_STORE_URL}
YAML
export SWARMKIT_STORE_URL="postgresql://swarm:hunter2@127.0.0.1:5433/swarmkit"
uv run swarmkit storage status "$WS"
echo
echo "  Note three things:"
echo "    - audit, artifacts, memory and saga all FOLLOWED runtime. One block moves the workspace."
echo "    - the password is masked. This report goes to terminals, logs and CI capture."
echo "    - '\${SWARMKIT_STORE_URL}' was EXPANDED. Nothing in the runtime did that before 1.130.0,"
echo "      so that exact line — the one every deployment doc uses — reached SQLAlchemy literally."

hr "3. checkpoints deliberately did NOT follow"
echo "  It is a LangGraph component with its own driver ('swarmkit-runtime[postgres]'). Promoting"
echo "  it because the APPLICATION store is Postgres would fail a workspace that never asked."

hr "4. A backend that cannot be honoured FAILS — it does not quietly degrade"
unset SWARMKIT_STORE_URL
uv run swarmkit storage status "$WS" 2>&1 | tail -3 || true
echo
echo "  Degrading to SQLite here would write the run to a different database than the configured"
echo "  one — the failure that started all of this, wearing a different hat."

hr "5. Local rows under a remote config are reported, not silently stranded"
export SWARMKIT_STORE_URL="postgresql://swarm:hunter2@127.0.0.1:5433/swarmkit"
uv run swarmkit storage migrate "$WS" --dry-run 2>&1 | tail -6 || true

hr "6. The same report is in serve"
echo "  GET /storage, and the System page in the web UI. The answer to 'why is this empty' has to"
echo "  be reachable from the screen that is empty."

rm -rf "$(dirname "$WS")"
