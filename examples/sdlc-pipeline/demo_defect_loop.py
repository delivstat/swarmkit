"""Demo: the controller-driven defect loop (slice 8 of sdlc-pipeline-example.md).

The centerpiece of slice 8. It drives the **reference pipeline controller** over the
``sdlc-sit-pt`` stage-graph to show the cross-stage defect cycle the design calls for
(design/details/pipeline-controller.md "Cross-stage defect loop"): a requirement reaches SIT,
SIT raises a ``defect.raised`` event -> the controller **re-kicks build**; the fix emits
``defect.fixed`` -> the controller **re-triggers SIT** -> SIT passes -> the saga proceeds through
PT and the pre-release security gate to ``done``.

Deterministic — no model calls, no harness, no live server, no network. The ``run_stage`` seam is
**scripted**: every stage completes cleanly except the gated ``security-review`` stage, which parks
on its gate until a scripted sign-off (the design's two-kinds-of-pause: a gate is a durable wait).
SIT and PT completions arrive as enterprise QA/perf events (``build.ready-in-qa`` / ``sit.passed`` /
``pt.passed`` — the mock rigs report results the same way CI does), so the controller waits on them
and the demo (playing the enterprise event source) injects them — which is also where it injects the
defect. Every run is stamped with its ``correlation_id`` so the saga timeline correlates.

Run it:

    uv run python examples/sdlc-pipeline/demo_defect_loop.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from controller import (
    InboundEvent,
    PipelineController,
    StageGraph,
    StageRunOutcome,
    StageRunRequest,
)
from swarmkit_runtime.resolver import resolve_workspace

WS = Path(__file__).resolve().parent / "workspace"

# The gated stage parks on its gate (the controller then waits for a gate-resolution); every other
# stage completes cleanly. The mock rigs (SIT / PT env) are the completed stage runs — no real
# systems, no network.
GATED_TOPOLOGIES = {"security-review"}

# The stage boundaries the controller waits on as EXTERNAL enterprise events (CI + the mock QA/perf
# rigs reporting their results), rather than signals it fabricates itself.
EXTERNAL_EVENTS = ("build.ready-in-qa", "sit.passed", "pt.passed")


def _load_graph() -> StageGraph:
    ws = resolve_workspace(WS)
    return StageGraph.from_spec(ws.stage_graphs["sdlc-sit-pt"].spec)


def make_seam(kicked: list[str]) -> object:
    async def run_stage(request: StageRunRequest) -> StageRunOutcome:
        # Correlation: every run carries its correlation_id (design "Run correlation label").
        kicked.append(f"{request.correlation_id}:{request.topology}")
        if request.topology in GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")

    return run_stage


# Timeline event kinds that mark the defect cycle, so the printed trace calls the loop out.
_LOOP_KINDS = {"loop.reentry"}


def print_timeline(controller: PipelineController, correlation_id: str) -> None:
    saga = controller.saga(correlation_id)
    assert saga is not None
    print(f"\n── correlated saga timeline: {correlation_id}  [status={saga.status.upper()}] ──")
    for entry in saga.timeline:
        stage = f"[{entry.stage_id}]" if entry.stage_id else "[-]"
        marker = "  ⟲ DEFECT LOOP" if entry.kind in _LOOP_KINDS else ""
        print(f"  {entry.seq:>3} {stage:<18} {entry.kind:<22} {entry.detail}{marker}")


def _stage_run_trail(kicked: list[str]) -> str:
    # The bare stage-topology sequence, so the loop reads at a glance: build -> sit -> build -> ...
    order = {"oms-build": "build", "sit": "sit", "pt": "pt", "security-review": "security-review"}
    return " → ".join(order.get(run.split(":", 1)[1], run.split(":", 1)[1]) for run in kicked)


async def main() -> None:
    kicked: list[str] = []
    controller = PipelineController(
        _load_graph(),
        make_seam(kicked),  # type: ignore[arg-type]
        external_events=EXTERNAL_EVENTS,
    )
    cid = "REQ-501"

    print(f"STEP 1 — {cid}: upstream design approved → build runs → waits on CI (build.ready)")
    await controller.handle_event(InboundEvent(cid, "design.approved", "design-gate-1"))

    print("STEP 2 — CI reports build ready → SIT runs the cross-app e2e flows against the mock rig")
    await controller.handle_event(InboundEvent(cid, "build.ready-in-qa", "ci-1"))
    print(f"   {cid} passed={controller.saga(cid).passed_stages}  (reached SIT)")

    print("\nSTEP 3 — SIT finds a defect → 'defect.raised' → the controller RE-KICKS BUILD (loop)")
    await controller.handle_event(InboundEvent(cid, "defect.raised", "qa-defect-1"))
    print(f"   oms-build re-kicked: {sum(1 for k in kicked if k.endswith('oms-build'))} build runs")

    print("STEP 4 — the fix lands → 'defect.fixed' → the controller RE-TRIGGERS SIT (loop)")
    await controller.handle_event(InboundEvent(cid, "defect.fixed", "dev-fix-1"))
    print(f"   sit re-triggered: {sum(1 for k in kicked if k.endswith(':sit'))} SIT runs")

    print("\nSTEP 5 — the re-run SIT passes (mock rig) → PT runs (mock rig) → PT passes")
    await controller.handle_event(InboundEvent(cid, "sit.passed", "qa-sit-pass-1"))
    await controller.handle_event(InboundEvent(cid, "pt.passed", "perf-pass-1"))
    print(f"   {cid} parked on gate={controller.saga(cid).pending_gate}  (pre-release security)")

    print("STEP 6 — the pre-release security review signs off → release approved → saga DONE")
    await controller.resolve_gate(cid, approved=True)
    print(f"   {cid} status={controller.saga(cid).status}")

    print_timeline(controller, cid)

    print("\n── stage-run trail (every run stamped with its correlation_id) ──")
    print(f"  {_stage_run_trail(kicked)}")
    print("  the loop: build → sit → defect.raised → build → sit → defect.fixed → … → done")

    saga = controller.saga(cid)
    assert saga is not None and saga.status == "done"
    assert sum(1 for k in kicked if k.endswith("oms-build")) == 2  # build re-kicked once
    assert sum(1 for k in kicked if k.endswith(":sit")) == 2  # sit re-triggered once
    print("\n✓ defect-loop demo complete")


if __name__ == "__main__":
    asyncio.run(main())
