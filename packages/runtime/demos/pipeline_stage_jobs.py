#!/usr/bin/env python
"""Demo: a pipeline's stages appear in job history, linked to the run.

Before this change a pipeline run wrote saga state and nothing else — `/runs` showed stage names
and statuses, `/jobs` showed an empty table, and what each stage actually produced or cost was
findable from neither. Now every stage leaves a job row keyed by its run id and tagged with the
correlation id of the pipeline that asked for it.

    uv run python packages/runtime/demos/pipeline_stage_jobs.py

The model is stubbed — the point is the bookkeeping, so the run stays free and offline.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.orchestration import SagaState
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage

CORRELATION = "WMS-5"
STAGES = [
    {"id": "design", "topology": "design-swarm"},
    {"id": "build", "topology": "build-swarm"},
]


class _Usage:
    input_tokens = 1200
    output_tokens = 340
    cost_usd = 0.42


class _Result:
    def __init__(self, output: str) -> None:
        self.output = output
        self.node_errors: dict[str, str] = {}
        self.usage = _Usage()


class _StubRuntime:
    """Stands in for the real runtime: returns a plausible stage output without calling a model."""

    def __init__(self, root: Path) -> None:
        self.workspace_root = root

    async def run(self, topology: str, _input: str, *, thread_id: str, **_kw: Any) -> _Result:
        print(f"  running {topology} as thread {thread_id}")
        return _Result(f"output of {topology}")


class _SagaStore:
    def __init__(self) -> None:
        self.saga = SagaState(correlation_id=CORRELATION, graph_id="wms", input="the ticket")

    def get(self, _cid: str) -> SagaState:
        return self.saga

    def save(self, _saga: SagaState) -> None:
        pass


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        (ws / ".swarmkit").mkdir(parents=True)

        # The ONE storage service — the same call serve makes. The stage takes the store it built;
        # it never opens its own, or a pipeline's jobs could land on a different backend from the
        # ones the UI lists.
        storage = storage_for_workspace(ws)
        store = storage.store()
        artifacts = build_artifact_store(
            None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 'a.sqlite'}"
        )

        run_stage = build_pipeline_run_stage(
            _StubRuntime(ws),  # type: ignore[arg-type]
            artifacts,
            _SagaStore(),  # type: ignore[arg-type]
            job_store=store,
        )

        # A standalone run, to show the two kinds side by side.
        store.create_job("adhoc-1", "hello", "say hi")
        store.update_job("adhoc-1", status="completed", completed_at="2026-08-05T10:00:00Z")

        print(f"running pipeline {CORRELATION}:")
        for stage in STAGES:
            outcome = await run_stage(CORRELATION, stage)
            print(f"  {stage['id']}: {outcome.status}")

        print("\nall recorded jobs:")
        for job in store.list_jobs():
            link = job.correlation_id or "-"
            print(f"  {job.id:<18} {job.topology:<14} {job.status:<10} pipeline={link}")

        print(f"\njust {CORRELATION}'s stages (GET /jobs/history?correlation_id={CORRELATION}):")
        for job in store.list_jobs(correlation_id=CORRELATION):
            cost = job.usage_cost_usd or 0.0
            print(f"  {job.id:<18} {job.topology:<14} ${cost:.2f}")

        print(
            f"\nEach id is also the trace's run_id: /observability/runs/{CORRELATION}:design/trace"
        )


if __name__ == "__main__":
    asyncio.run(main())
