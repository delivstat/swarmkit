"""Demo: the FULL SDLC lifecycle — the capstone of the sdlc-pipeline example (slice 9).

This is the finale that stitches the whole program together. It drives the **reference pipeline
controller** over the ``sdlc-full`` stage-graph and takes ONE requirement (``OMS-101``) through the
ENTIRE software-delivery lifecycle, every stage in order:

    intake -> design -> build -> sit -> pt -> security-review -> deploy -> support-handover -> done

correlated end to end by its ``correlation_id`` into one saga timeline (the DORA / audit view). It
carries the two multi-party human gates (the consolidated-design gate at design; the pre-release
security gate), the final release sign-off (deploy: eng-manager + cio), the integration-contract
locks held through design approval, and — in the graph, exercised by ``demo_defect_loop.py`` — the
cross-stage defect loop.

Deterministic — no model calls, no harness, no live server, no network. The ``run_stage`` seam is
**scripted** (mock rigs): the three gated stages (consolidated design, security review, deploy) park
on their gate until a scripted human sign-off; every other stage completes cleanly. The three stage
boundaries that are real enterprise events — CI build-ready and the mock QA / perf rigs reporting
their results (``build.ready-in-qa`` / ``sit.passed`` / ``pt.passed``) — arrive externally, so the
controller waits on them and the demo (playing the enterprise event source) injects them.

Run it (this is ``just demo-sdlc``):

    uv run python examples/sdlc-pipeline/demo_full_sdlc.py
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

# The gated stages park on their gate (the controller then waits for a gate-resolution); every
# other stage completes cleanly. This mirrors the design's two-kinds-of-pause: a gate is a durable
# wait. Three human gates across the lifecycle: the design gate, the pre-release security gate, and
# the final release sign-off.
GATED_TOPOLOGIES = {"consolidated-design", "security-review", "deploy"}

# The stage boundaries the controller waits on as EXTERNAL enterprise events (CI + the mock QA/perf
# rigs reporting their results), rather than signals it fabricates itself.
EXTERNAL_EVENTS = ("build.ready-in-qa", "sit.passed", "pt.passed")

# A human-readable one-liner per stage, for the journey summary.
_STAGE_STORY = {
    "intake": "business-analyst ingests the BRD → impact analysis + per-app design kickoff",
    "design": "per-app architects + integration-architect → consolidated design (4-party gate)",
    "build": "the developer HARNESS implements the design → candidate diff + code-review",
    "sit": "sit-qa runs the cross-app e2e business flows against the mock rig",
    "pt": "pt-engineer runs perf on the exposed services against the mock rig (pt-analysis)",
    "security-review": "security-consultant HARNESS review + infosec-lead pre-release sign-off",
    "deploy": "release-coordinator assembles the deploy package + release notes (eng-mgr + cio)",
    "support-handover": "support-engineer writes the runbook / handover + sets up prod monitoring",
}


def _load_graph() -> StageGraph:
    ws = resolve_workspace(WS)
    return StageGraph.from_spec(ws.stage_graphs["sdlc-full"].spec)


def make_seam(kicked: list[StageRunRequest]) -> object:
    async def run_stage(request: StageRunRequest) -> StageRunOutcome:
        # Correlation: every run carries its correlation_id (design "Run correlation label").
        kicked.append(request)
        if request.topology in GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")

    return run_stage


def print_timeline(controller: PipelineController, correlation_id: str) -> None:
    saga = controller.saga(correlation_id)
    assert saga is not None
    print(f"\n── correlated saga timeline: {correlation_id}  [status={saga.status.upper()}] ──")
    for entry in saga.timeline:
        stage = f"[{entry.stage_id}]" if entry.stage_id else "[-]"
        print(f"  {entry.seq:>3} {stage:<20} {entry.kind:<22} {entry.detail}")


def print_journey(kicked: list[StageRunRequest]) -> None:
    # The stage-by-stage journey, each run stamped with its correlation_id — a human-readable saga.
    print("\n── the lifecycle journey (every stage, in order, correlated end to end) ──")
    for i, req in enumerate(kicked, start=1):
        story = _STAGE_STORY.get(req.stage_id, "")
        print(f"  {i}. {req.stage_id:<18} [{req.correlation_id}]  {story}")


async def main() -> None:
    kicked: list[StageRunRequest] = []
    controller = PipelineController(
        _load_graph(),
        make_seam(kicked),  # type: ignore[arg-type]
        external_events=EXTERNAL_EVENTS,
    )
    cid = "OMS-101"

    print(f"═══ Driving {cid} through the ENTIRE SDLC lifecycle (sdlc-full stage-graph) ═══\n")

    print(f"STEP 1 — {cid} enters the pipeline: intake runs → design kicks off →")
    print("         the consolidated design is drafted and parks on the multi-party design gate")
    await controller.handle_event(InboundEvent(cid, "requirement.created", "jira-1"))
    s = controller.saga(cid)
    assert s is not None
    print(f"   status={s.status}  gate={s.pending_gate}  locks={sorted(s.held_locks)}")

    print("\nSTEP 2 — app leads + infosec-lead APPROVE the design → locks released → build runs")
    print("         (the developer harness produces a diff, code-review passes) → waits on CI")
    await controller.resolve_gate(cid, approved=True)
    print(f"   status={s.status}  passed={s.passed_stages}  locks={sorted(s.held_locks)}")

    print("\nSTEP 3 — CI reports build ready → SIT runs the cross-app e2e flows (mock rig) → pass")
    await controller.handle_event(InboundEvent(cid, "build.ready-in-qa", "ci-1"))
    await controller.handle_event(InboundEvent(cid, "sit.passed", "qa-sit-1"))
    print(f"   passed={s.passed_stages}")

    print("\nSTEP 4 — PT runs perf on the exposed services (mock rig) → passes →")
    print("         the pre-release SECURITY review parks on its gate (harness review + infosec)")
    await controller.handle_event(InboundEvent(cid, "pt.passed", "perf-1"))
    print(f"   status={s.status}  gate={s.pending_gate}")

    print("\nSTEP 5 — infosec-lead signs off the security review → DEPLOY packaging runs →")
    print("         it parks on the final release sign-off (eng-manager + cio)")
    await controller.resolve_gate(cid, approved=True)
    print(f"   status={s.status}  gate={s.pending_gate}")

    print("\nSTEP 6 — eng-manager + cio APPROVE the release → support-handover runs → saga DONE")
    await controller.resolve_gate(cid, approved=True)
    print(f"   status={s.status}  passed={s.passed_stages}")

    print_timeline(controller, cid)
    print_journey(kicked)

    saga = controller.saga(cid)
    assert saga is not None and saga.status == "done"
    expected = [
        "intake",
        "design",
        "build",
        "sit",
        "pt",
        "security-review",
        "deploy",
        "support-handover",
    ]
    assert saga.passed_stages == expected, saga.passed_stages
    assert [r.stage_id for r in kicked] == expected
    assert all(r.correlation_id == cid for r in kicked)

    print(
        f"\n  {cid} shipped: BRD → consolidated design (4-party gate) → harness build →"
        " SIT → PT → security sign-off → deploy package (eng-manager + cio) → support handover."
    )
    print("  One requirement, eight bounded stages, three human gates, one correlated audit trail.")
    print("\n✓ full SDLC demo complete")


if __name__ == "__main__":
    asyncio.run(main())
