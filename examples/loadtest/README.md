# Load test / NFR harness

Establish the non-functional characteristics of `swarmkit serve` — throughput, the latency knee,
admission behaviour, and memory/FD growth — **deterministically and for free**, with no real model
and no network egress. Full write-up and measured numbers:
[load and scale](../../docs/site/reference/load-and-scale.md).

## Pieces

- **Latency mock.** `SWARMKIT_MOCK_LATENCY_MS` (+ `SWARMKIT_MOCK_LATENCY_JITTER_MS`) makes the mock
  provider sleep per model call, so the benchmark measures the runtime under realistic concurrency
  instead of an instant-return mock. Without it you are timing JSON + the DB, not an agent runtime.
- **Three topology shapes** (`workspaces/`): `tiny` (1 agent, 1 call — API/scheduler/audit
  overhead), `typical` (leader → 3 workers — fan-out + aggregation; run with `delegate=1`),
  `mcp-heavy` (4 MCP-backed tools through the governed gateway; uses `mcp/mock_mcp.py`, a stdio MCP
  server with its own `MOCK_MCP_LATENCY_MS`).
- **Driver** (`driver/loadtest.py`): `ramp` holds N in-flight runs per level and reports throughput
  + submit/run p50/p95/p99 + 429 rate, sampling the serve process's RSS and open fds from `/proc`;
  `storm` fires N submissions at a low `max_concurrent` to measure the admission/429 path. httpx +
  the standard library — no k6, no psutil.

## Run

```bash
# Concurrency ramp on the tiny shape, 2s simulated model latency:
examples/loadtest/run.sh tiny tiny 2000 "5,10,25,50,100" 20

# Fan-out shape (leader delegates to three workers):
examples/loadtest/run.sh typical typical 2000 "5,25,100" 20 1

# Realistic write load — point at Postgres first:
export SWARMKIT_STORE_URL="postgresql://user:pw@127.0.0.1:5432/swarmkit"
examples/loadtest/run.sh tiny tiny 2000 "5,25,50" 20

# Admission storm — 10k submits at max_concurrent=5 (serve a workspace with that cap):
uv run python examples/loadtest/driver/loadtest.py storm --topology tiny --n 10000
```

Chaos (kill -9 during a run) is a regression test: `packages/runtime/tests/test_kill9_recovery.py`.
The long-soak, real-provider, and >1000-concurrency scenarios are procedures in the write-up — run
them on your own hardware.
