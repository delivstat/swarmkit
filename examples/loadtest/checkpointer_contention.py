"""How much of a run is spent queued on the checkpointer lock? (load-and-scale.md, Run 6)

Every LangGraph async saver serializes its I/O behind one ``asyncio.Lock`` per saver instance
(``AsyncPostgresSaver._cursor``, ``AsyncSqliteSaver``'s ``self.lock``). The runtime builds ONE
saver per process, so every concurrently running run in that process — all-in-one ``serve`` at
``max_concurrent`` > 1, or a fan-out within a run — queues on that lock for every checkpoint read
and write. A worker runs one job at a time and is not affected, unless the run itself fans out.

This drives N concurrent ``WorkspaceRuntime.run`` calls in-process (no HTTP, no driver in the
measurement) for a fixed window, wraps the saver's lock to time wait-vs-hold, and reports:

- throughput (runs/s) and run latency p50/p95
- ``wait_ms/run``: summed time spent WAITING to acquire a saver lock, per completed run. LangGraph
  issues some checkpoint writes as background tasks that overlap, so this can exceed the run's own
  wall time; compare it with ``hold_ms/run`` (the lock's real work) rather than reading it as a
  fraction.

    uv run python examples/loadtest/checkpointer_contention.py \
        --workspace examples/loadtest/workspaces/tiny --topology tiny \
        --checkpoints sqlite --levels 1,5,25,50 --duration 15 --latency-ms 500

    # Postgres checkpoints (the store stays wherever SWARMKIT_STORE_URL points):
    uv run python examples/loadtest/checkpointer_contention.py ... \
        --checkpoints postgres --checkpoints-url postgresql://lt:lt@127.0.0.1:55432/swarmkit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any


class _TimedLock:
    """An ``asyncio.Lock`` stand-in that records how long acquirers wait and hold."""

    def __init__(self, inner: asyncio.Lock) -> None:
        self._inner = inner
        self.wait_s = 0.0
        self.hold_s = 0.0
        self.acquisitions = 0
        self._held_at = 0.0

    async def acquire(self) -> bool:
        t0 = time.perf_counter()
        await self._inner.acquire()
        self._held_at = time.perf_counter()
        self.wait_s += self._held_at - t0
        self.acquisitions += 1
        return True

    def release(self) -> None:
        self.hold_s += time.perf_counter() - self._held_at
        self._inner.release()

    def locked(self) -> bool:
        return self._inner.locked()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *exc: object) -> None:
        self.release()


def _workspace_copy(src: Path, checkpoints: str, url: str | None) -> Path:
    """Copy the workspace and pin `storage.checkpoints` — the env store URL never moves it."""
    dst = Path(tempfile.mkdtemp(prefix="lt-ckpt-")) / src.name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".swarmkit"))
    ws = dst / "workspace.yaml"
    block = f"\nstorage:\n  checkpoints:\n    backend: {checkpoints}\n"
    if url:
        block += f"    url: {url}\n"
    ws.write_text(ws.read_text() + block)
    return dst


def _locks(saver: Any) -> list[tuple[Any, str]]:
    """The lock attribute(s) a saver serializes on — one per shard when the saver is sharded."""
    savers = getattr(saver, "shards", [saver])
    return [(s, "lock") for s in savers if isinstance(getattr(s, "lock", None), asyncio.Lock)]


async def _level(rt: Any, topology: str, conc: int, duration: float) -> dict[str, Any]:
    saver = await rt._ensure_checkpointer()
    timers: list[tuple[Any, str, _TimedLock]] = []
    for owner, attr in _locks(saver):
        t = _TimedLock(getattr(owner, attr))
        setattr(owner, attr, t)
        timers.append((owner, attr, t))

    latencies: list[float] = []
    stop_at = time.perf_counter() + duration

    async def user() -> None:
        while time.perf_counter() < stop_at:
            t0 = time.perf_counter()
            await rt.run(topology, "ping")
            latencies.append(time.perf_counter() - t0)

    t_start, cpu_start = time.perf_counter(), time.process_time()
    await asyncio.gather(*(user() for _ in range(conc)))
    elapsed = time.perf_counter() - t_start
    cpu = time.process_time() - cpu_start

    for owner, attr, t in timers:
        setattr(owner, attr, t._inner)
    wait = sum(t.wait_s for *_, t in timers)
    hold = sum(t.hold_s for *_, t in timers)
    q = statistics.quantiles(latencies, n=20) if len(latencies) >= 2 else [0.0] * 19
    return {
        "concurrency": conc,
        "completed": len(latencies),
        "throughput_rps": round(len(latencies) / elapsed, 2),
        "run_p50_ms": round(statistics.median(latencies) * 1000) if latencies else 0,
        "run_p95_ms": round(q[18] * 1000),
        "lock_acquisitions_per_run": round(
            sum(t.acquisitions for *_, t in timers) / max(len(latencies), 1), 1
        ),
        "lock_hold_ms_per_run": round(hold / max(len(latencies), 1) * 1000, 1),
        "lock_wait_ms_per_run": round(wait / max(len(latencies), 1) * 1000, 1),
        # Process CPU (all threads) per wall second: ~1.0 means one core is saturated — the loop
        # thread is the ceiling, whatever the locks are doing.
        "cpu_cores": round(cpu / elapsed, 2),
        "cpu_ms_per_run": round(cpu / max(len(latencies), 1) * 1000, 1),
        "saver": type(saver).__name__,
        "shards": len(getattr(saver, "shards", [saver])),
    }


async def _main(args: argparse.Namespace) -> None:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    os.environ["SWARMKIT_MOCK_LATENCY_MS"] = str(args.latency_ms)
    os.environ["SWARMKIT_MOCK_LATENCY_JITTER_MS"] = str(args.latency_ms // 4)

    ws = _workspace_copy(Path(args.workspace).resolve(), args.checkpoints, args.checkpoints_url)
    rt = WorkspaceRuntime.from_workspace_path(ws)
    await rt._ensure_checkpointer()
    await rt.run(args.topology, "warmup")  # compile + first-connection cost out of the window

    rows = []
    shards = len(getattr(await rt._ensure_checkpointer(), "shards", [None]))
    print(
        f"checkpoints={args.checkpoints} shards={shards} topology={args.topology} "
        f"latency={args.latency_ms}ms"
    )
    print("conc  runs/s  p50_ms  p95_ms  hold_ms/run  wait_ms/run  cpu_cores  cpu_ms/run")
    for conc in (int(x) for x in args.levels.split(",")):
        r = await _level(rt, args.topology, conc, args.duration)
        rows.append(r)
        print(
            f"{conc:<5} {r['throughput_rps']:<7} {r['run_p50_ms']:<7} {r['run_p95_ms']:<7} "
            f"{r['lock_hold_ms_per_run']:<12} {r['lock_wait_ms_per_run']:<12} "
            f"{r['cpu_cores']:<10} {r['cpu_ms_per_run']}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))
    await rt.aclose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--workspace", required=True)
    p.add_argument("--topology", default="tiny")
    p.add_argument("--checkpoints", choices=["sqlite", "postgres"], default="sqlite")
    p.add_argument("--checkpoints-url", default=None)
    p.add_argument("--levels", default="1,5,25,50")
    p.add_argument("--duration", type=float, default=15.0)
    p.add_argument("--latency-ms", type=int, default=500)
    p.add_argument("--out", default=None)
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
