"""Run 5, isolated worker throughput (docs/site/reference/load-and-scale.md).

The ramp in run5.sh sends work through one `serve --role api` and reads results back through it, so
its numbers fold in the api tier's synchronous enqueue + status I/O on a single event loop. To
measure the thing worker-execution.md set out to lift — execution throughput across worker
processes — this seeds a fixed backlog of queued jobs straight into Postgres, starts N workers, and
times how long they take to drain it by polling the durable row counts. No api tier in the loop.

    SWARMKIT_STORE_URL=postgresql+psycopg://... uv run python examples/loadtest/run5_drain.py \
        --workspace examples/loadtest/workspaces/typical --topology typical \
        --backlog 240 --workers 1,2,4,8 --latency-ms 500
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select
from swarmkit_runtime.persistence._store import make_engine
from swarmkit_runtime.persistence._tables import jobs


def _seed(engine, topology: str, n: int) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC).isoformat()
    rows = [
        {
            "id": uuid.uuid4().hex[:12],
            "topology": topology,
            "input": "drain",
            "status": "queued",
            "created_at": now,
        }
        for _ in range(n)
    ]
    with engine.begin() as conn:
        conn.execute(delete(jobs))
        conn.execute(insert(jobs), rows)


def _count(engine, status: str) -> int:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        return int(conn.execute(select(func.count()).where(jobs.c.status == status)).scalar() or 0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--topology", default="typical")
    p.add_argument("--backlog", type=int, default=240)
    p.add_argument("--workers", default="1,2,4,8")
    p.add_argument("--latency-ms", type=int, default=500)
    p.add_argument("--timeout", type=float, default=300.0)
    args = p.parse_args()

    url = os.environ["SWARMKIT_STORE_URL"]
    engine = make_engine(url)
    env = {
        **os.environ,
        "SWARMKIT_PROVIDER": "mock",
        "SWARMKIT_MOCK_LATENCY_MS": str(args.latency_ms),
        "SWARMKIT_MOCK_LATENCY_JITTER_MS": str(args.latency_ms // 4),
        # Small per-worker pool so N workers stay under Postgres max_connections.
        "SWARMKIT_STORE_POOL_SIZE": os.environ.get("SWARMKIT_STORE_POOL_SIZE", "4"),
        "SWARMKIT_STORE_MAX_OVERFLOW": os.environ.get("SWARMKIT_STORE_MAX_OVERFLOW", "4"),
    }

    print(
        f"backlog={args.backlog} topology={args.topology} "
        f"latency={args.latency_ms}ms store=postgres"
    )
    print("workers  drain_s  throughput_rps  per_worker_rps  scaling_vs_1")
    base = None
    for n in (int(x) for x in args.workers.split(",")):
        _seed(engine, args.topology, args.backlog)
        # start_new_session so each `uv run` wrapper leads its own process group; killpg then
        # takes the wrapper AND the swarmkit worker child. terminate() on the wrapper alone can
        # orphan the child, which would silently inflate the NEXT config's worker count.
        procs = [
            subprocess.Popen(
                ["uv", "run", "swarmkit", "worker", args.workspace, "--poll-seconds", "0.1"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(n)
        ]
        time.sleep(3.0)  # let workers boot + open pools before timing
        t0 = time.perf_counter()
        deadline = t0 + args.timeout
        while _count(engine, "completed") < args.backlog and time.perf_counter() < deadline:
            time.sleep(0.2)
        drain = time.perf_counter() - t0
        for pr in procs:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pr.pid), signal.SIGTERM)
        for pr in procs:
            pr.wait()
        done = _count(engine, "completed")
        rps = done / drain if drain > 0 else 0.0
        base = base or rps
        print(f"{n:<8} {drain:<8.1f} {rps:<15.2f} {rps / n:<15.2f} {rps / base:.2f}x")

    engine.dispose()


if __name__ == "__main__":
    main()
