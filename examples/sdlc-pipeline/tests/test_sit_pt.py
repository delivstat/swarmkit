"""Slice 8 — cross-app SIT + PT (mock rigs), the pre-release security gate, and the defect loop.

Covers design/details/sdlc-pipeline-example.md (build order item 8) + pipeline-controller.md
(the defect loop):

  - the cross-app ``sit`` / ``pt`` topologies + the ``security-review`` topology resolve + compile,
    with the right archetypes, cross-app read scopes, and the pre-release funnel bound;
  - the ``security-review-approval`` funnel resolves — its ``security-consultant`` harness review is
    layer 3 (route_back_at: high) and its ``infosec-lead`` approver confers ``security:approve``;
  - the ``sdlc-sit-pt`` stage-graph resolves + ref-checks (build → sit → pt → security-review + the
    defect loop);
  - the SIT / PT mock rigs + the ``pt-analysis`` determination behave (pass / regression / defect);
  - the security gate advances on a clean review and routes a HIGH finding back before passing;
  - **the centerpiece**: driving the reference controller over the graph, ``defect.raised`` re-kicks
    build, ``defect.fixed`` re-triggers sit, and the saga reaches ``done``.

The example ships under ``examples/sdlc-pipeline`` (not an installed runtime feature), so the
directory is put on ``sys.path`` to import the demo modules — same as the other slice tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers._mock import MockModelProvider
from swarmkit_runtime.resolver import resolve_workspace

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "examples" / "sdlc-pipeline"
_WS = _EXAMPLE / "workspace"
if str(_EXAMPLE) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE))

import demo_defect_loop as loop  # type: ignore[import-not-found]  # noqa: E402
import sit_pt  # type: ignore[import-not-found]  # noqa: E402
from controller import (  # type: ignore[import-not-found]  # noqa: E402
    InboundEvent,
    PipelineController,
    StageGraph,
    StageRunOutcome,
    StageRunRequest,
)

# --------------------------------------------------------------------------------------------
# Topologies: SIT + PT + security-review resolve, compile, and are scoped correctly
# --------------------------------------------------------------------------------------------


def test_sit_topology_is_cross_app_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["sit"]
    root = topo.root
    assert root.id == "sit-runner"
    assert root.source_archetype == "sit-qa"
    scopes = (root.iam or {}).get("base_scope", [])
    # cross-app: reads across all three apps (the shared surface) + the mock SIT rig.
    for app in ("oms", "web", "mobile"):
        assert f"app:{app}:read" in scopes
    assert "sit:read" in scopes

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


def test_pt_topology_is_cross_app_uses_pt_analysis_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["pt"]
    root = topo.root
    assert root.id == "pt-runner"
    assert root.source_archetype == "pt-engineer"
    scopes = (root.iam or {}).get("base_scope", [])
    for app in ("oms", "web", "mobile"):
        assert f"app:{app}:read" in scopes
    assert "pt:read" in scopes
    # the pt-engineer archetype carries the pt-analysis decision skill.
    assert "pt-analysis" in [s.id for s in root.skills]

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


def test_security_review_topology_binds_the_prerelease_funnel_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["security-review"]
    root = topo.root
    assert root.source_archetype == "release-coordinator"
    assert root.funnel is not None and root.funnel.id == "security-review-approval"

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


# --------------------------------------------------------------------------------------------
# The pre-release security funnel: security-consultant harness review + infosec-lead sign-off
# --------------------------------------------------------------------------------------------


def test_security_funnel_has_harness_review_and_resolving_roles() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.funnels["security-review-approval"].spec
    registry = ws.role_registry

    # layer 3 is the investigative security-consultant harness review, routing back at HIGH.
    assert spec["review"]["archetype"] == "security-consultant"
    assert spec["review"]["route_back_at"] == "high"
    # the security-consultant is a harness executor (investigative outside review).
    reviewer = ws.archetypes["security-consultant"]
    assert reviewer.executor is not None and reviewer.executor.kind == "harness"

    # the approver role resolves and confers the scope it is asked to sign off.
    for rule in spec["approve"]["rules"]:
        for role in rule["roles"]:
            assert registry.get(role) is not None
            assert registry.confers(role, rule["scope"])


# --------------------------------------------------------------------------------------------
# The stage-graph: build -> sit -> pt -> security-review + the defect loop, ref-checked
# --------------------------------------------------------------------------------------------


def test_stage_graph_resolves_with_sit_pt_release_and_defect_loop() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.stage_graphs["sdlc-sit-pt"].spec
    stages = {s["id"]: s for s in spec["stages"]}
    assert set(stages) == {"build", "sit", "pt", "security-review"}
    assert stages["sit"]["topology"] == "sit"
    assert stages["pt"]["topology"] == "pt"
    assert stages["security-review"]["gate"] == "security-review-approval"
    # the defect loop: defect.raised -> build, defect.fixed -> sit.
    loops = {loop_["when"]: loop_["to"] for loop_ in spec["loops"]}
    assert loops == {"defect.raised": "build", "defect.fixed": "sit"}


# --------------------------------------------------------------------------------------------
# The mock rigs + pt-analysis determination
# --------------------------------------------------------------------------------------------


def test_sit_rig_passes_clean_and_files_a_defect_on_a_failing_flow() -> None:
    assert all(f.passed for f in sit_pt.run_sit_rig())
    failed = [f for f in sit_pt.run_sit_rig(failing_flow="checkout") if not f.passed]
    assert len(failed) == 1
    assert "checkout" in failed[0].flow and failed[0].detail


def test_pt_analysis_passes_within_thresholds_and_fails_on_regression() -> None:
    ok = sit_pt.pt_analysis(sit_pt.run_pt_rig())
    assert ok.verdict == "pass" and ok.breaches == ()
    regressed = sit_pt.pt_analysis(sit_pt.run_pt_rig(regression=True))
    assert regressed.verdict == "fail"
    assert any("order-api" in b for b in regressed.breaches)  # the injected latency regression


# --------------------------------------------------------------------------------------------
# The security-review gate: advance on clean, route the HIGH finding back then pass
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_gate_advances_on_clean_review() -> None:
    ws = resolve_workspace(_WS)
    run = await sit_pt.run_security_review_gate(
        ws, review_script=["clean"], correlation_id="TEST-SEC-1"
    )
    assert run.outcome == "approved"
    assert run.retries == 0
    assert run.approvers == frozenset({"dana"})  # the infosec-lead signed off


@pytest.mark.asyncio
async def test_security_gate_routes_high_finding_back_then_passes() -> None:
    ws = resolve_workspace(_WS)
    run = await sit_pt.run_security_review_gate(
        ws, review_script=["high", "clean"], correlation_id="TEST-SEC-2"
    )
    assert run.retries == 1  # the HIGH finding routed back once
    assert run.outcome == "approved"
    assert run.approvers == frozenset({"dana"})


# --------------------------------------------------------------------------------------------
# The centerpiece: the controller-driven defect loop over the real stage-graph
# --------------------------------------------------------------------------------------------


def _graph() -> StageGraph:
    ws = resolve_workspace(_WS)
    return StageGraph.from_spec(ws.stage_graphs["sdlc-sit-pt"].spec)


class _Seam:
    """A scripted run_stage seam: parks the gated security stage, completes every other stage."""

    def __init__(self) -> None:
        self.calls: list[StageRunRequest] = []

    def kicked(self, topology: str) -> int:
        return sum(1 for c in self.calls if c.topology == topology)

    async def __call__(self, request: StageRunRequest) -> StageRunOutcome:
        self.calls.append(request)
        if request.topology in loop.GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")


@pytest.mark.asyncio
async def test_defect_loop_rekicks_build_and_retriggers_sit_then_reaches_done() -> None:
    seam = _Seam()
    controller = PipelineController(_graph(), seam, external_events=loop.EXTERNAL_EVENTS)
    cid = "REQ-8"

    # Drive to SIT: design approved -> build -> (CI) build.ready-in-qa -> sit.
    await controller.handle_event(InboundEvent(cid, "design.approved", "d1"))
    await controller.handle_event(InboundEvent(cid, "build.ready-in-qa", "ci1"))
    assert seam.kicked("oms-build") == 1
    assert seam.kicked("sit") == 1  # SIT ran against the mock rig

    # SIT raises a defect -> the controller RE-KICKS BUILD (loop re-entry).
    await controller.handle_event(InboundEvent(cid, "defect.raised", "qa1"))
    assert seam.kicked("oms-build") == 2
    assert any(e.kind == "loop.reentry" for e in controller.timeline(cid))

    # The fix -> the controller RE-TRIGGERS SIT.
    await controller.handle_event(InboundEvent(cid, "defect.fixed", "dev1"))
    assert seam.kicked("sit") == 2

    # The re-run SIT passes -> PT -> the pre-release security gate -> sign-off -> done.
    await controller.handle_event(InboundEvent(cid, "sit.passed", "qa-pass"))
    await controller.handle_event(InboundEvent(cid, "pt.passed", "perf-pass"))
    assert controller.saga(cid).pending_gate == "security-review-approval"
    await controller.resolve_gate(cid, approved=True)

    saga = controller.saga(cid)
    assert saga is not None and saga.status == "done"
    # every run correlates by the correlation id (the DORA/audit view).
    assert all(c.correlation_id == cid for c in seam.calls)
    assert all(e.correlation_id == cid for e in controller.timeline(cid))


@pytest.mark.asyncio
async def test_defect_loop_demo_main_runs_green() -> None:
    """The shipped demo runs end to end and reaches the done saga (the acceptance gate)."""
    await loop.main()
