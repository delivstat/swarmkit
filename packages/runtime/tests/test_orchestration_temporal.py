"""Temporal orchestration adapter — the pipeline as a durable Temporal workflow (slice 5+).

Runs under Temporal's in-process time-skipping WorkflowEnvironment (no external server). Gated
`integration` (deselected by default) and `importorskip`-guarded so CI without temporalio skips
cleanly; run locally with `uv pip install temporalio` + `pytest -m integration`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("temporalio")

from temporalio.testing import WorkflowEnvironment

# The orchestrator lives in the sdlc example (a reference component, not runtime code).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples" / "sdlc-pipeline"))
from orchestrator import StageOutcome  # type: ignore[import-not-found]
from orchestrator.temporal import (  # type: ignore[import-not-found]
    TemporalOrchestrator,
    pipeline_worker,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_GRAPH: dict[str, Any] = {
    "stages": [
        {
            "id": "intake",
            "topology": "oms-intake",
            "when": ["requirement.created"],
            "success": "design.kickoff",
        },
        {
            "id": "design",
            "topology": "oms-design",
            "when": ["design.kickoff"],
            "gate": "oms-design-gate",
            "success": "design.approved",
            "compensation": "oms-compensate-design",
        },
        {
            "id": "build",
            "topology": "oms-build",
            "when": ["design.approved"],
            "success": "build.ready-in-qa",
        },
        {
            "id": "sit",
            "topology": "oms-sit",
            "when": ["build.ready-in-qa"],
            "success": "sit.passed",
        },
    ],
    "loops": [{"when": "defect.raised", "to": "design"}],
}


def _scripted_run_stage(record: list[str]):
    async def run_stage(requirement_id: str, stage: dict[str, Any]) -> StageOutcome:
        record.append(f"{requirement_id}:{stage['id']}:{stage['topology']}")
        # A gated stage produces then parks on its funnel gate (resolved via a signal).
        if stage.get("gate"):
            return StageOutcome(status="parked", artifact=f"<{stage['id']}>")
        return StageOutcome(status="completed", artifact=f"<{stage['id']}>")

    return run_stage


async def test_pipeline_runs_to_done_with_gate_approval() -> None:
    record: list[str] = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        orch = TemporalOrchestrator(env.client, _scripted_run_stage(record))
        async with pipeline_worker(env.client, orch):
            await orch.start("RT-1", _GRAPH, "requirement.created")
            # Parks on the design gate.
            await _until(lambda: _pending(orch, "RT-1"))
            state = await orch.state("RT-1")
            assert state.pending_gate == "oms-design-gate"
            assert state.passed_stages == ["intake"]
            # Approve → runs to completion.
            await orch.resolve_gate("RT-1", "oms-design-gate", approved=True)
            result = await orch.result("RT-1")
    assert result["status"] == "done"
    assert result["passed_stages"] == ["intake", "design", "build", "sit"]
    assert [r.split(":")[1] for r in record] == ["intake", "design", "build", "sit"]


async def test_gate_rejection_stops_the_pipeline() -> None:
    record: list[str] = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        orch = TemporalOrchestrator(env.client, _scripted_run_stage(record))
        async with pipeline_worker(env.client, orch):
            await orch.start("RT-2", _GRAPH, "requirement.created")
            await _until(lambda: _pending(orch, "RT-2"))
            await orch.resolve_gate("RT-2", "oms-design-gate", approved=False)
            result = await orch.result("RT-2")
    assert result["status"] == "rejected"
    assert "build" not in [r.split(":")[1] for r in record]


async def test_cancel_compensates_passed_stages_in_reverse() -> None:
    record: list[str] = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        orch = TemporalOrchestrator(env.client, _scripted_run_stage(record))
        async with pipeline_worker(env.client, orch):
            await orch.start("RT-3", _GRAPH, "requirement.created")
            await _until(lambda: _pending(orch, "RT-3"))  # intake passed, parked on design gate
            await orch.cancel("RT-3")
            result = await orch.result("RT-3")
    assert result["status"] == "cancelled"
    # intake passed (no compensation declared); design was still parked, not passed → no comp run.
    assert "oms-compensate-design" not in [r.split(":")[2] for r in record if ":" in r]


# ---- helpers -------------------------------------------------------------------------------


async def _pending(orch: TemporalOrchestrator, req: str) -> bool:
    try:
        return (await orch.state(req)).pending_gate is not None
    except Exception:
        return False


async def _until(cond: Any) -> None:
    for _ in range(200):
        if await cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")
