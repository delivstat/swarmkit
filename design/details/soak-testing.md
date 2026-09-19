---
title: Soak testing — the leak/stability matrix serve must survive
description: A soak harness and matrix (steady / churn / approval / harness) that runs serve under sustained load for hours and watches the slope of RSS, fds, DB connections and queue age — because a burst passes where a soak fails. Follow-up to load-and-scale.md from the scaling review.
tags: [serve, scaling, operations, testing]
status: proposed
---

# Soak testing

## The gap

`load-and-scale.md` measured throughput and latency in short bursts. A review noted what bursts
cannot catch: a run that leaks a little memory, an fd, a DB connection or a worktree per iteration
looks fine for 20 seconds and falls over at hour six. The most important soak result is not "the
process stayed alive" but that the *slopes* are flat: **RSS ≈ 0, fds ≈ 0, DB connections bounded,
queue age stable, and zero orphans** (jobs, subprocesses, worktrees).

## Goal

- A **soak mode** in the load harness (`examples/loadtest/driver/loadtest.py soak`) that holds a
  steady, below-capacity load and samples RSS + open fds over time, reporting the slope and a
  flat/growing verdict — not a knee-finding ramp.
- A **matrix** of four soaks, each targeting a different leak surface, with explicit pass criteria.
- A representative short run recorded in `load-and-scale.md`; the long/multi-process soaks are a
  runbook to run on real hardware (they are hours long and want a dedicated box).

## The matrix

| Soak | Load shape | Watches | Pass |
|---|---|---|---|
| **Steady** | stable arrival below capacity, no restarts, hours | RSS, fds, DB connections, task count | RSS slope ≈ 0, fd slope ≈ 0, connections bounded |
| **Churn** | steady load + periodically restart workers, stop-all-then-resume, rotate API instances, interrupt Postgres | reclaim correctness, connection recovery, RSS after each cycle | no orphan `running` rows, no leaked connections, RSS returns to baseline after each cycle |
| **Approval** | park many runs on human gates for hours, resolve in bursts | worker-slot occupancy, queue rows | **no worker slot held while parked** (a deferred run releases its worker — worker-execution.md), rows stable |
| **Harness** | repeated harness runs creating/removing worktrees + spawning/terminating subprocesses, with timeouts and cancellations | worktrees, subprocesses, fds | zero orphan worktrees, zero orphan subprocesses after timeout/cancel |

The one metric that matters across all four:

```
RSS slope ≈ 0 · fd slope ≈ 0 · DB connections bounded · queue age stable
orphan jobs = 0 · orphan subprocesses = 0 · orphan worktrees = 0
```

## Test / demo plan

- The `soak` subcommand: steady load, periodic RSS/fd sampling, slope + verdict, optional JSON
  time-series out. Verified by a short representative run (all-in-one serve, mock provider) in
  `load-and-scale.md` — a smoke soak, proving the harness and a flat short-window slope.
- The full matrix is a **runbook**: it needs hours and, for churn/harness, a Postgres + worker pool
  and a real harness workspace, so it is documented to run on a dedicated box rather than asserted
  in CI. Each soak's pass criteria above is the acceptance check.

## Non-goals

- Not a CI job — a multi-hour soak does not belong in the PR gate; the harness makes it a one-command
  runbook, and the short steady run is the smoke that CI-adjacent verification can afford.
- Not a fix for a leak it might find — this is the instrument. A leak it surfaces is its own fix.
