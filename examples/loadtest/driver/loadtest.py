"""A dependency-light load driver for `swarmkit serve` (docs/site/reference/load-and-scale.md).

Two workloads against a running serve:

- **ramp** — for each concurrency level, hold that many in-flight runs (submit `POST /run`, poll
  `GET /jobs/{id}` to completion) for a fixed duration; report throughput (runs/s), submit and
  end-to-end p50/p95/p99, and the 429 rate. The point is the *knee*: where p95 turns up.
- **storm** — fire N submissions as fast as possible at a low `max_concurrent`; report how cleanly
  serve rejects (429 rate + submit p99). Admission throughput, separate from execution.
- **soak** — hold a steady, below-capacity load for a long duration and sample the serve process's
  RSS and open fds over time; report the *slope* (soak-testing.md). A burst passes where a soak
  fails: a leak shows as a trend, not a spike.

Samples the serve process's RSS and open file descriptors from `/proc`. No k6, no psutil — httpx +
the standard library.

    uv run python examples/loadtest/driver/loadtest.py ramp --topology tiny --levels 5,10,25,50,100
    uv run python examples/loadtest/driver/loadtest.py storm --topology tiny --n 10000
    uv run python examples/loadtest/driver/loadtest.py soak --topology tiny --concurrency 5
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


def _slope_per_min(ts: list[float], ys: list[float]) -> float:
    """Least-squares slope of ys vs ts (seconds), returned per minute. 0.0 for <2 points or a flat
    time axis. Robust to the per-sample oscillation of a noisy series (open-fd counts), where a
    first-vs-last estimate would be dominated by which instant the last sample happened to catch."""
    n = len(ts)
    if n < 2:
        return 0.0
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    var_t = sum((t - mean_t) ** 2 for t in ts)
    if var_t == 0:
        return 0.0
    cov = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys, strict=False))
    return round((cov / var_t) * 60.0, 2)


@dataclass
class Sample:
    submit_ms: list[float] = field(default_factory=list)
    run_ms: list[float] = field(default_factory=list)
    completed: int = 0
    busy: int = 0
    errors: int = 0


_TERMINAL = ("completed", "failed", "stopped", "deferred")


async def _wait_poll(client: httpx.AsyncClient, api: str, job_id: str, t0: float) -> tuple:
    """Poll GET /jobs/{id} to completion. Adds up to one poll interval of detection latency per
    run, and one GET round-trip per interval — which is itself load on the API tier."""
    for _ in range(600):  # up to ~600 * poll_interval
        try:
            j = await client.get(f"{api}/jobs/{job_id}")
        except httpx.HTTPError:
            return (None, "error")
        status = j.json().get("status")
        if status in _TERMINAL:
            return ((time.perf_counter() - t0) * 1000, status)
        await asyncio.sleep(0.1)
    return (None, "timeout")


async def _wait_stream(client: httpx.AsyncClient, api: str, job_id: str, t0: float) -> tuple:
    """Follow GET /jobs/{id}/stream (SSE) to the `[done]` terminator. One long-lived connection
    per run instead of a poll loop — no repeated GETs competing with POST /run admission, and
    completion is detected when the server emits it, not on the next client tick. This is the
    driver Run 5 uses so the poller does not mask the api+workers throughput."""
    try:
        async with client.stream("GET", f"{api}/jobs/{job_id}/stream") as r:
            if r.status_code != 200:
                return (None, "error")
            async for line in r.aiter_lines():
                if line.startswith("data: [done] status="):
                    status = line.split("status=", 1)[1].strip()
                    run_ms = (time.perf_counter() - t0) * 1000
                    return (run_ms, status if status in _TERMINAL else "error")
    except httpx.HTTPError:
        return (None, "error")
    return (None, "timeout")


async def _one_run(
    client: httpx.AsyncClient, api: str, topology: str, payload: str, stream: bool
) -> tuple:
    """Submit one run and wait for it to finish. Returns (submit_ms, run_ms|None, status)."""
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
    run_ms, status = await (
        _wait_stream(client, api, job_id, t0) if stream else _wait_poll(client, api, job_id, t0)
    )
    return (submit_ms, run_ms, status)


async def _hold(
    client: httpx.AsyncClient, api: str, topology: str, sample: Sample, stop: float, stream: bool
) -> None:
    """A single virtual user: run after run until the deadline."""
    while time.perf_counter() < stop:
        submit_ms, run_ms, status = await _one_run(client, api, topology, "load", stream)
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
            start = time.perf_counter()
            stop = start + args.duration
            users = [
                asyncio.create_task(
                    _hold(client, args.api, args.topology, sample, stop, args.stream)
                )
                for _ in range(level)
            ]
            await asyncio.gather(*users)
            # Real wall time, NOT args.duration. A user starts its last run just before `stop` and
            # then waits for it — under a deep queue backlog (offered load >> capacity) that wait is
            # tens of seconds, so gather returns well after `stop`. Dividing completions by the
            # intended duration instead of the actual span overstates throughput badly there
            # (~2x on the api+workers ramp). Throughput is completions / time-actually-taken.
            elapsed = time.perf_counter() - start
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


async def soak(args: argparse.Namespace) -> None:
    """Hold a steady, below-capacity load for a long duration and sample the serve process's RSS
    and open-fd count over time (soak-testing.md). A burst passes where a soak fails: the point is
    the *slope* — RSS and fds should be flat, not climbing. Prints a time series and a verdict.

    Deliberately steady and modest (not a knee-finding ramp): a leak shows as a trend, and a trend
    needs a stable workload and hours, not a spike and 20 seconds."""
    samples: list[dict[str, float]] = []
    sample = Sample()
    start = time.perf_counter()
    stop = start + args.duration
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        users = [
            asyncio.create_task(_hold(client, args.api, args.topology, sample, stop, args.stream))
            for _ in range(args.concurrency)
        ]

        async def sampler() -> None:
            while time.perf_counter() < stop:
                rss, fds = _proc_stats(args.pid) if args.pid else (0.0, 0)
                t = time.perf_counter() - start
                samples.append({"t": round(t, 1), "rss_mb": round(rss, 1), "fds": fds})
                print(f"  t={t:6.0f}s  rss={rss:8.1f}MB  fds={fds:<5} completed={sample.completed}")
                await asyncio.sleep(args.sample_interval)

        await asyncio.gather(sampler(), *users)

    # Least-squares slope per minute over the STEADY window. Two deliberate choices: drop the first
    # sample (idle baseline taken before load ramped — comparing it to under-load samples reports
    # warmup, cache + pool fill, as a leak), and use a regression rather than first-vs-last because
    # fds oscillate per sampling instant (how many SSE/poll connections happen to be open) and a
    # two-point estimate on noise is meaningless. A real leak keeps climbing past warmup — which is
    # why the honest verdict needs the hours-long runbook, not a two-minute smoke.
    steady = samples[1:] if len(samples) >= 3 else samples
    rss_slope = _slope_per_min([s["t"] for s in steady], [s["rss_mb"] for s in steady])
    fd_slope = _slope_per_min([s["t"] for s in steady], [float(s["fds"]) for s in steady])
    # Window-aware verdict. Under ~10 min the RSS slope is warmup tail (caches, the compiled-graph
    # cache, connection pools filling and settling) and cannot be told apart from a slow leak, so
    # the honest label is INCONCLUSIVE rather than a confident FLAT or an alarmist GROWING. Only a
    # long soak (the runbook) earns a leak verdict; this mode's short use is a harness smoke.
    if args.duration < 600:
        verdict = "INCONCLUSIVE (short window — warmup dominates; run the hours-long soak)"
    elif abs(rss_slope) < 5 and abs(fd_slope) < 5:
        verdict = "FLAT (no leak signal)"
    else:
        verdict = "GROWING (investigate — slope persists past warmup)"
    print(
        f"\nsoak c={args.concurrency} for {args.duration:.0f}s: completed={sample.completed} "
        f"errors={sample.errors} 429={sample.busy}\n"
        f"  RSS slope {rss_slope:+.2f} MB/min · fd slope {fd_slope:+.2f} /min → {verdict}"
    )
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(json.dumps({"samples": samples, "rss_slope_mb_per_min": rss_slope}, indent=2))
        print(f"wrote {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ramp", "storm", "soak"):
        sp = sub.add_parser(name)
        sp.add_argument("--api", default=os.environ.get("LT_API", "http://127.0.0.1:8125"))
        sp.add_argument("--topology", default="tiny")
        sp.add_argument("--timeout", type=float, default=120.0)
        sp.add_argument("--pid", type=int, default=int(os.environ.get("LT_SERVE_PID") or "0"))
        if name == "ramp":
            sp.add_argument("--levels", default="5,10,25,50,100,250")
            sp.add_argument("--duration", type=float, default=20.0)
            sp.add_argument("--out", default="")
            sp.add_argument(
                "--stream",
                action="store_true",
                help="Follow each run over SSE instead of polling GET /jobs/{id} (Run 5).",
            )
        elif name == "soak":
            sp.add_argument("--concurrency", type=int, default=5)
            sp.add_argument("--duration", type=float, default=3600.0)
            sp.add_argument("--sample-interval", type=float, default=30.0)
            sp.add_argument("--out", default="")
            sp.add_argument("--stream", action="store_true")
        else:
            sp.add_argument("--n", type=int, default=10000)
            sp.add_argument("--connections", type=int, default=200)
    args = p.parse_args()
    runner = {"ramp": ramp, "storm": storm, "soak": soak}[args.cmd]
    asyncio.run(runner(args))


if __name__ == "__main__":
    main()
