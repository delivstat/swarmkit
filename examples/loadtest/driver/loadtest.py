"""A dependency-light load driver for `swarmkit serve` (docs/site/reference/load-and-scale.md).

Two workloads against a running serve:

- **ramp** — for each concurrency level, hold that many in-flight runs (submit `POST /run`, poll
  `GET /jobs/{id}` to completion) for a fixed duration; report throughput (runs/s), submit and
  end-to-end p50/p95/p99, and the 429 rate. The point is the *knee*: where p95 turns up.
- **storm** — fire N submissions as fast as possible at a low `max_concurrent`; report how cleanly
  serve rejects (429 rate + submit p99). Admission throughput, separate from execution.

It also samples the serve process's RSS and open file descriptors from `/proc` on an interval, so
a run doubles as a soak/leak probe. No k6, no psutil — httpx + the standard library.

    uv run python examples/loadtest/driver/loadtest.py ramp --topology tiny --levels 5,10,25,50,100
    uv run python examples/loadtest/driver/loadtest.py storm --topology tiny --n 10000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field

import httpx


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round((p / 100) * (len(xs) - 1))))
    return xs[k]


@dataclass
class Sample:
    submit_ms: list[float] = field(default_factory=list)
    run_ms: list[float] = field(default_factory=list)
    completed: int = 0
    busy: int = 0
    errors: int = 0


async def _one_run(client: httpx.AsyncClient, api: str, topology: str, payload: str) -> tuple:
    """Submit one run and poll it to completion. Returns (submit_ms, run_ms|None, status)."""
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{api}/run/{topology}", json={"input": payload})
    except httpx.HTTPError:
        return (0.0, None, "error")
    submit_ms = (time.perf_counter() - t0) * 1000
    if r.status_code == 429:
        return (submit_ms, None, "busy")
    if r.status_code != 200:
        return (submit_ms, None, "error")
    job_id = r.json().get("job_id")
    for _ in range(600):  # up to ~600 * poll_interval
        try:
            j = await client.get(f"{api}/jobs/{job_id}")
        except httpx.HTTPError:
            return (submit_ms, None, "error")
        status = j.json().get("status")
        if status in ("completed", "failed", "stopped", "deferred"):
            return (submit_ms, (time.perf_counter() - t0) * 1000, status)
        await asyncio.sleep(0.1)
    return (submit_ms, None, "timeout")


async def _hold(
    client: httpx.AsyncClient, api: str, topology: str, sample: Sample, stop: float
) -> None:
    """A single virtual user: run after run until the deadline."""
    while time.perf_counter() < stop:
        submit_ms, run_ms, status = await _one_run(client, api, topology, "load")
        sample.submit_ms.append(submit_ms)
        if status == "busy":
            sample.busy += 1
        elif run_ms is not None and status == "completed":
            sample.run_ms.append(run_ms)
            sample.completed += 1
        else:
            sample.errors += 1


def _proc_stats(pid: int) -> tuple[float, int]:
    """(RSS MB, open fd count) for a pid from /proc; (0, 0) if unavailable."""
    try:
        rss_kb = 0
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        fds = len(os.listdir(f"/proc/{pid}/fd"))
        return (rss_kb / 1024, fds)
    except OSError:
        return (0.0, 0)


async def ramp(args: argparse.Namespace) -> None:
    levels = [int(x) for x in args.levels.split(",")]
    rows = []
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for level in levels:
            sample = Sample()
            stop = time.perf_counter() + args.duration
            users = [
                asyncio.create_task(_hold(client, args.api, args.topology, sample, stop))
                for _ in range(level)
            ]
            await asyncio.gather(*users)
            elapsed = args.duration
            rss, fds = _proc_stats(args.pid) if args.pid else (0.0, 0)
            row = {
                "concurrency": level,
                "throughput_rps": round(sample.completed / elapsed, 2),
                "submit_p50_ms": round(_pct(sample.submit_ms, 50), 1),
                "submit_p95_ms": round(_pct(sample.submit_ms, 95), 1),
                "run_p50_ms": round(_pct(sample.run_ms, 50), 1),
                "run_p95_ms": round(_pct(sample.run_ms, 95), 1),
                "run_p99_ms": round(_pct(sample.run_ms, 99), 1),
                "completed": sample.completed,
                "busy_429": sample.busy,
                "errors": sample.errors,
                "rss_mb": round(rss, 1),
                "open_fds": fds,
            }
            rows.append(row)
            print(
                f"c={level:<5} rps={row['throughput_rps']:<7} "
                f"run_p50={row['run_p50_ms']:<8} run_p95={row['run_p95_ms']:<9} "
                f"run_p99={row['run_p99_ms']:<9} 429={row['busy_429']:<4} err={row['errors']:<4} "
                f"rss={row['rss_mb']}MB fds={row['open_fds']}"
            )
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(json.dumps(rows, indent=2))
        print(f"\nwrote {args.out}")


async def storm(args: argparse.Namespace) -> None:
    sample = Sample()

    async def submit(client: httpx.AsyncClient) -> None:
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{args.api}/run/{args.topology}", json={"input": "x"})
        except httpx.HTTPError:
            sample.errors += 1
            return
        sample.submit_ms.append((time.perf_counter() - t0) * 1000)
        if r.status_code == 429:
            sample.busy += 1
        elif r.status_code == 200:
            sample.completed += 1
        else:
            sample.errors += 1

    limits = httpx.Limits(max_connections=args.connections)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        sem = asyncio.Semaphore(args.connections)

        async def one() -> None:
            async with sem:
                await submit(client)

        await asyncio.gather(*[one() for _ in range(args.n)])
    wall = time.perf_counter() - t0
    print(
        f"storm n={args.n} in {wall:.1f}s → {args.n / wall:.0f} submit/s | "
        f"accepted={sample.completed} 429={sample.busy} err={sample.errors} | "
        f"submit p50={_pct(sample.submit_ms, 50):.1f}ms p99={_pct(sample.submit_ms, 99):.1f}ms"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ramp", "storm"):
        sp = sub.add_parser(name)
        sp.add_argument("--api", default=os.environ.get("LT_API", "http://127.0.0.1:8125"))
        sp.add_argument("--topology", default="tiny")
        sp.add_argument("--timeout", type=float, default=120.0)
        sp.add_argument("--pid", type=int, default=int(os.environ.get("LT_SERVE_PID") or "0"))
        if name == "ramp":
            sp.add_argument("--levels", default="5,10,25,50,100,250")
            sp.add_argument("--duration", type=float, default=20.0)
            sp.add_argument("--out", default="")
        else:
            sp.add_argument("--n", type=int, default=10000)
            sp.add_argument("--connections", type=int, default=200)
    args = p.parse_args()
    asyncio.run(ramp(args) if args.cmd == "ramp" else storm(args))


if __name__ == "__main__":
    main()
