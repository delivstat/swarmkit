"""Demo: the SDLC pipeline controller (design/details/pipeline-controller.md, slice 5).

Drives one OMS requirement through intake -> design (parks on the approval gate; a scripted
approval advances it) -> build -> sit, then exercises the hard parts of a real saga — all with a
**scripted** ``run_stage`` seam: no model calls, no API budget. The controller owns durable
per-requirement state; SwarmKit (here, the stub) only runs bounded stages.

Shows:
  (a) a duplicate webhook is a no-op (idempotency key dedup);
  (b) a *dropped* ``build.ready-in-qa`` webhook recovered by reconciliation (the safety net);
  (c) a second concurrent requirement contending on the same integration contract — it parks on
      the lock and serialises behind the first, then acquires on release;
  (d) a cancellation that unwinds already-passed stages with their compensations, in reverse.
Prints the correlated saga timeline for each requirement.

Run it:

    uv run python examples/sdlc-pipeline/demo_pipeline_controller.py
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
    SurfaceNotice,
)
from swarmkit_runtime.resolver import resolve_workspace

WS = Path(__file__).resolve().parent / "workspace"

# Gated stages park on their gate (the controller then waits for a gate-resolution); every other
# stage completes cleanly. This mirrors the design's two-kinds-of-pause: a gate is a durable wait.
GATED_TOPOLOGIES = {"oms-design"}


def _load_graph() -> StageGraph:
    ws = resolve_workspace(WS)
    return StageGraph.from_spec(ws.stage_graphs["oms-pipeline"].spec)


def make_seam(kicked: list[str]) -> object:
    async def run_stage(request: StageRunRequest) -> StageRunOutcome:
        # Correlation: every run carries its requirement_id (design "Run correlation label").
        label = "compensate " if request.is_compensation else ""
        kicked.append(f"{request.requirement_id}:{label}{request.topology}")
        if request.is_compensation:
            return StageRunOutcome(status="completed")
        if request.topology in GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")

    return run_stage


def print_timeline(controller: PipelineController, requirement_id: str) -> None:
    saga = controller.saga(requirement_id)
    assert saga is not None
    print(f"\n── correlated saga timeline: {requirement_id}  [status={saga.status.upper()}] ──")
    for entry in saga.timeline:
        stage = f"[{entry.stage_id}]" if entry.stage_id else "[-]"
        print(f"  {entry.seq:>3} {stage:<10} {entry.kind:<22} {entry.detail}")


async def main() -> None:
    surfaced: list[SurfaceNotice] = []
    closed_gates: list[tuple[str, str]] = []
    kicked: list[str] = []

    # build.ready-in-qa is an EXTERNAL CI event: the controller never fabricates it. In the demo
    # its webhook is "dropped" for OMS-101, so reconciliation must recover it from source state.
    source_confirmed: dict[str, set[str]] = {}

    controller = PipelineController(
        _load_graph(),
        make_seam(kicked),  # type: ignore[arg-type]
        external_events=("build.ready-in-qa",),
        source_state=lambda rid: source_confirmed.get(rid, set()),
        on_surface=surfaced.append,
        on_close_gate=lambda rid, gate: closed_gates.append((rid, gate)),
    )

    print("STEP 1 — OMS-101 enters the pipeline (intake -> design parks on the gate)")
    await controller.handle_event(InboundEvent("OMS-101", "requirement.created", "jira-1"))
    s1 = controller.saga("OMS-101")
    assert s1 is not None
    print(
        f"   OMS-101 status={s1.status}  pending_gate={s1.pending_gate}"
        f"  locks={sorted(s1.held_locks)}"
    )

    print("\nSTEP 2 (a) — a duplicate 'requirement.created' webhook for OMS-101 is a no-op")
    await controller.handle_event(InboundEvent("OMS-101", "requirement.created", "jira-1"))

    print("\nSTEP 3 (c) — OMS-102 arrives and contends on the same integration contract")
    await controller.handle_event(InboundEvent("OMS-102", "requirement.created", "jira-2"))
    s2 = controller.saga("OMS-102")
    assert s2 is not None
    print(
        f"   OMS-102 status={s2.status}  pending_lock_stage={s2.pending_lock_stage}"
        f"  (contract held by OMS-101 → parked, serialised)"
    )

    print("\nSTEP 4 — OMS-101 design gate APPROVED → contract released → OMS-102 resumes")
    await controller.resolve_gate("OMS-101", approved=True)
    print(f"   OMS-101 status={s1.status}  locks={sorted(s1.held_locks)}  (advanced to build)")
    print(
        f"   OMS-102 status={s2.status}  locks={sorted(s2.held_locks)}"
        f"  pending_gate={s2.pending_gate}  (acquired contract, now on its own gate)"
    )

    print("\nSTEP 5 — OMS-102 design gate APPROVED → advances to build (external CI wait)")
    await controller.resolve_gate("OMS-102", approved=True)

    print(
        "\nSTEP 6 (b) — OMS-101's 'build.ready-in-qa' webhook was DROPPED; reconciliation recovers"
    )
    source_confirmed["OMS-101"] = {"build.ready-in-qa"}  # source systems show build is ready
    await controller.reconcile("OMS-101")
    print(
        f"   OMS-101 status={s1.status}  passed={s1.passed_stages}"
        f"  (reconciliation drove SIT → done)"
    )

    print("\nSTEP 7 (d) — OMS-102 is withdrawn mid-pipeline → compensations run in reverse")
    await controller.cancel("OMS-102", detail="requirement withdrawn by product")
    print(f"   OMS-102 status={s2.status}")

    print_timeline(controller, "OMS-101")
    print_timeline(controller, "OMS-102")

    print("\n── correlated stage runs (every run stamped with its requirement_id) ──")
    for run in kicked:
        print(f"  · {run}")
    print(f"\n  gate tasks closed on resolve/cancel: {closed_gates}")
    print(f"  surfaced to a human: {[n.reason for n in surfaced] or 'none'}")
    print("\n✓ pipeline-controller demo complete")


if __name__ == "__main__":
    asyncio.run(main())
