"""Demo: the OMS pipeline driven by the **Temporal** orchestrator
(design/details/orchestration-provider-seam.md).

The real ``oms-pipeline`` StageGraph runs as a durable Temporal workflow under the in-process
time-skipping test environment (no server, no model calls) — proving the seam: SwarmKit's
StageGraph + governed stage runs, sequenced by Temporal instead of the hand-rolled controller.

Shows: intake -> design (parks on its funnel gate, resolved by a signal) -> build -> sit -> done;
then a second requirement cancelled mid-flight, unwinding with compensation.

Requires the orchestrator group (temporalio) — pulled in on demand, no separate sync:
Run it:  uv run --group orchestrator python examples/sdlc-pipeline/demo_pipeline_temporal.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "examples" / "sdlc-pipeline"))

from orchestrator import StageOutcome  # type: ignore[import-not-found]  # noqa: E402
from orchestrator.temporal import (  # type: ignore[import-not-found]  # noqa: E402
    TemporalOrchestrator,
    pipeline_worker,
)
from swarmkit_runtime.resolver import resolve_workspace  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402

GRAPH: dict[str, Any] = (
    resolve_workspace(_ROOT / "examples" / "sdlc-pipeline" / "workspace")
    .stage_graphs["oms-pipeline"]
    .spec
)


def _run_stage(record: list[str]) -> Any:
    async def run_stage(correlation_id: str, stage: dict[str, Any]) -> StageOutcome:
        record.append(stage["id"])
        if stage.get("gate"):  # produce, then park on the funnel gate (resolved via a signal)
            return StageOutcome(status="parked", artifact=f"<{stage['id']}>")
        return StageOutcome(status="completed", artifact=f"<{stage['id']}>")

    return run_stage


async def main() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        rec1: list[str] = []
        orch = TemporalOrchestrator(env.client, _run_stage(rec1))
        async with pipeline_worker(env.client, orch):
            print("① OMS-500 through the pipeline on Temporal (gate approved)")
            await orch.start("OMS-500", GRAPH, "requirement.created")
            while (await orch.state("OMS-500")).pending_gate is None:
                await asyncio.sleep(0.02)
            st = await orch.state("OMS-500")
            print(f"   parked on gate {st.pending_gate!r} after stages {st.passed_stages}")
            await orch.resolve_gate("OMS-500", str(st.pending_gate), approved=True)
            result = await orch.result("OMS-500")
            print(f"   STATUS: {result['status'].upper()}  stages: {result['passed_stages']}")
            print(f"   stage runs: {rec1}\n")

            print("② OMS-501 cancelled mid-flight → unwinds")
            await orch.start("OMS-501", GRAPH, "requirement.created")
            while (await orch.state("OMS-501")).pending_gate is None:
                await asyncio.sleep(0.02)
            await orch.cancel("OMS-501")
            result = await orch.result("OMS-501")
            print(f"   STATUS: {result['status'].upper()} (locks released, compensations run)\n")

    print("✓ pipeline-temporal demo complete")


if __name__ == "__main__":
    asyncio.run(main())
