# Feature-flag cleanup

Retiring stale feature flags at scale, as **data** — the SwarmKit shape of DoorDash's agentic
flag-cleanup system ([case study](../../docs/site/case-studies/feature-flag-cleanup.md)).

Two phases, both governed:

1. **Triage** (`flag-triage`) — a model agent reads a stale flag's rollout metadata and every
   reference, and writes a cleanup report. The `intake-review` funnel validates the report's shape
   and takes a **human confirmation** before any code changes.
2. **Cleanup** (`flag-cleanup`) — a **harness** agent removes the flag in an isolated git worktree,
   runs the build and tests, and produces a candidate diff. The `cleanup-review` funnel judges the
   diff (a finding routes the critique back for a bounded revision) and takes a **human sign-off**
   — the only exit, and where the PR is opened. Bounded by a budget (`max_wall_clock_minutes: 60`).

The daily fan-out across repositories is the calling application's job; each governed cleanup is one
run here.

```bash
uv run python examples/flag-cleanup/demo.py     # deterministic — no keys, no network
swarmkit validate examples/flag-cleanup/workspace --require --require-verified
```

The demo drives the **real** bundled `claude-code` adapter against a scripted `stream-json`
transcript (only the subprocess launch is faked) through the real funnel gate — so the diff, the
tool trail, the cost, and the human sign-off all flow through the same code a live run would.
