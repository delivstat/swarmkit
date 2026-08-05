#!/usr/bin/env python
"""Demo: what the dashboard now has to show, and where it comes from.

    uv run python packages/runtime/demos/dashboard_data.py

Records runs from several front doors, then prints the two things the dashboard reads: the durable
job history (with each run's source) and the per-model usage breakdown behind `/usage`.

Before this change the page read the in-memory job store — which holds only what the current serve
process started — and `/usage` was fed by that one path too, so a workspace driven from the CLI, a
pipeline or chat had an empty dashboard and a blank cost breakdown.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from swarmkit_runtime.persistence import storage_for_workspace, usage_fields


class _Usage:
    def __init__(self, cost: float) -> None:
        self.input_tokens = 1200
        self.output_tokens = 340
        self.cost_usd = 0.0
        self.by_model = {"anthropic/claude-opus-5": {"input": 1200, "output": 340, "cost": cost}}


#: (job id, topology, source, correlation, status, cost)
RUNS = [
    ("run-a", "wms-triage", "cli", None, "completed", 0.42),
    ("WMS-5:design", "design-swarm", "pipeline", "WMS-5", "completed", 1.10),
    ("WMS-5:build", "build-swarm", "pipeline", "WMS-5", "failed", 0.30),
    ("c1:1", "advisor", "chat", "c1", "completed", 0.05),
    ("job-77", "wms-triage", "serve", None, "completed", 0.18),
]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        (ws / ".swarmkit").mkdir(parents=True)
        store = storage_for_workspace(ws).store()

        for job_id, topology, source, correlation, status, cost in RUNS:
            store.create_job(job_id, topology, "the input", correlation, source)
            fields = usage_fields(_Usage(cost), job_id, store)
            store.update_job(job_id, status=status, completed_at="2026-08-06T12:00:00Z", **fields)

        print("what /jobs/history returns:\n")
        print(f"  {'job':<16} {'topology':<14} {'source':<10} {'status':<10} cost")
        for job in store.list_jobs():
            print(
                f"  {job.id:<16} {job.topology:<14} {job.source or '-':<10} "
                f"{job.status:<10} ${job.usage_cost_usd or 0:.2f}"
            )

        total = sum(j.usage_cost_usd or 0 for j in store.list_jobs())
        failed = sum(1 for j in store.list_jobs() if j.status == "failed")
        finished = sum(1 for j in store.list_jobs() if j.status in ("completed", "failed"))
        print(
            f"\nactivity: {len(RUNS)} runs, {failed} failed ({failed / finished:.0%}), ${total:.2f}"
        )

        print("\nwhat /usage returns — the per-model breakdown:")
        for row in store.get_usage_by_model():
            print(
                f"  {row['model']:<26} {row['calls']} calls  "
                f"{row['input_tokens']} in / {row['output_tokens']} out  ${row['cost_usd']:.2f}"
            )
        print("\nEvery one of those rows came from a path that used to write none.")


if __name__ == "__main__":
    main()
