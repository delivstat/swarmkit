"""Each pipeline stage gets its own run id.

`_pipeline_stage` passed `thread_id=correlation_id` for every stage. That id becomes both the
LangGraph checkpoint thread and the trace's `run_id`, and a trace saves to `{run_id}.json` — so
stage 2 overwrote stage 1's trace and a three-stage run left exactly one file, the last stage's.
Per-stage cost and tool history were destroyed as the run progressed, silently.

Sharing a checkpoint thread was wrong on its own terms too: stages run **different topologies**, so
each stage resumed into graph state left by a different graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.server._pipeline_stage import stage_run_id
from swarmkit_runtime.trace import RunTrace


def _saved_traces(root: Path) -> list[str]:
    return sorted(p.name for p in (root / ".swarmkit" / "traces").glob("*.json"))


def test_stage_run_ids_are_distinct_and_correlated() -> None:
    a = stage_run_id("WMS-9", "triage")
    b = stage_run_id("WMS-9", "design")
    assert a != b
    assert a.startswith("WMS-9") and b.startswith("WMS-9"), "still findable as one run"


def test_three_stages_leave_three_traces(tmp_path: Path) -> None:
    """The regression, stated as the thing a user loses: two of three stages' traces."""
    for stage in ("triage", "design", "build"):
        trace = RunTrace()
        trace.start(stage_run_id("WMS-9", stage), stage)
        trace.save(tmp_path)

    files = _saved_traces(tmp_path)
    assert len(files) == 3, f"one file per stage; got {files}"


def test_the_old_shape_loses_all_but_the_last(tmp_path: Path) -> None:
    """Pins the bug so a revert is loud rather than quiet: with the bare correlation id, three
    stages collapse to one file and the survivor is whichever ran last."""
    for stage in ("triage", "design", "build"):
        trace = RunTrace()
        trace.start("WMS-9", stage)  # the old behaviour
        trace.save(tmp_path)

    files = _saved_traces(tmp_path)
    assert files == ["WMS-9.json"]
    survivor = json.loads((tmp_path / ".swarmkit" / "traces" / files[0]).read_text())
    assert survivor["topology"] == "build", "only the last stage survived"


@pytest.mark.asyncio
async def test_the_stage_runner_uses_a_per_stage_thread(tmp_path: Path) -> None:
    """End to end at the seam: what `run_stage` hands to `runtime.run` must be stage-qualified,
    because that one argument decides the trace file, the checkpoint thread and the audit run id."""
    import shutil  # noqa: PLC0415

    from swarmkit_runtime.artifacts import build_artifact_store  # noqa: PLC0415
    from swarmkit_runtime.server._pipeline_stage import (  # noqa: PLC0415
        build_pipeline_run_stage,
    )

    repo = Path(__file__).resolve().parents[3]
    ws = tmp_path / "ws"
    shutil.copytree(repo / "examples" / "hello-swarm" / "workspace", ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)
    (ws / ".swarmkit").mkdir()

    seen: list[str] = []

    class _Result:
        output = "done"

    class _Runtime:
        workspace_root = ws

        async def run(self, *a: Any, **k: Any) -> Any:
            seen.append(str(k.get("thread_id")))
            return _Result()

    class _SagaStore:
        class _Saga:
            input = "payload"
            passed_stages: list[str] = []  # noqa: RUF012

        def get(self, _cid: str) -> Any:
            return self._Saga()

    run_stage = build_pipeline_run_stage(
        _Runtime(),  # type: ignore[arg-type]
        build_artifact_store(
            None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 's.sqlite'}"
        ),
        _SagaStore(),  # type: ignore[arg-type]
    )
    await run_stage("WMS-9", {"id": "triage", "topology": "hello"})
    await run_stage("WMS-9", {"id": "design", "topology": "hello"})

    assert seen == ["WMS-9:triage", "WMS-9:design"]
    assert len(set(seen)) == 2, "two stages of one run must not share a thread"
