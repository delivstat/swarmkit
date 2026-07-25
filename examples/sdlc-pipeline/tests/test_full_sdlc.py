"""Slice 9 — the full SDLC lifecycle (the capstone).

Covers design/details/sdlc-pipeline-example.md (build order item 9):

  - the ``deploy`` + ``support-handover`` topologies resolve + compile, with the right archetypes,
    cross-app read scopes, and — for deploy — the final release funnel bound;
  - the ``deploy-approval`` funnel resolves — its ``eng-manager`` + ``cio`` approvers confer the
    human-only ``release:approve`` scope, quorum ``all``, four-eyes floor of 2;
  - the ``sdlc-full`` stage-graph resolves + ref-checks: the eight stages in order, the two
    multi-party gates + the final release gate, the contract locks, the compensations, and the
    cross-stage defect loop;
  - **the capstone**: driving the reference controller over ``sdlc-full`` takes one requirement
    through the ENTIRE lifecycle intake -> design -> build -> sit -> pt -> security-review ->
    deploy -> support-handover -> done, all stages passed in order, correlated end to end;
  - the shipped ``demo_full_sdlc`` runs green (the acceptance gate).

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

import demo_full_sdlc as full  # type: ignore[import-not-found]  # noqa: E402
from controller import (  # type: ignore[import-not-found]  # noqa: E402
    InboundEvent,
    PipelineController,
    StageGraph,
    StageRunOutcome,
    StageRunRequest,
)

_LIFECYCLE = [
    "intake",
    "design",
    "build",
    "sit",
    "pt",
    "security-review",
    "deploy",
    "support-handover",
]


# --------------------------------------------------------------------------------------------
# Topologies: deploy + support-handover resolve, compile, and are scoped correctly
# --------------------------------------------------------------------------------------------


def test_deploy_topology_binds_the_release_funnel_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["deploy"]
    root = topo.root
    assert root.id == "deploy-coordinator"
    assert root.source_archetype == "release-coordinator"
    assert root.funnel is not None and root.funnel.id == "deploy-approval"
    scopes = (root.iam or {}).get("base_scope", [])
    # cross-app read: it assembles the package from all three apps' approved + built artifacts.
    for app in ("oms", "web", "mobile"):
        assert f"app:{app}:read" in scopes

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


def test_support_handover_topology_is_ungated_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["support-handover"]
    root = topo.root
    assert root.id == "handover-runner"
    assert root.source_archetype == "support-engineer"
    assert root.funnel is None  # terminal operability artifact, not a binding gate
    scopes = (root.iam or {}).get("base_scope", [])
    assert "kb:write" in scopes  # writes the shared runbook / handover KB

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


# --------------------------------------------------------------------------------------------
# The final release funnel: eng-manager + cio confer the human-only release:approve scope
# --------------------------------------------------------------------------------------------


def test_deploy_funnel_requires_eng_manager_and_cio_release_approve() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.funnels["deploy-approval"].spec
    registry = ws.role_registry

    rules = spec["approve"]["rules"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["scope"] == "release:approve"
    assert set(rule["roles"]) == {"eng-manager", "cio"}
    assert rule["quorum"] == "all"
    # four-eyes: two distinct humans must sign off, and the author is excluded.
    assert spec["approve"]["min_distinct_approvers"] == 2
    assert spec["approve"]["exclude_author"] is True

    # both approver roles resolve and confer the human-only scope they sign off.
    for role in rule["roles"]:
        assert registry.get(role) is not None
        assert registry.confers(role, "release:approve")


# --------------------------------------------------------------------------------------------
# The stage-graph: the eight-stage lifecycle + gates + locks + the defect loop, ref-checked
# --------------------------------------------------------------------------------------------


def test_stage_graph_is_the_full_lifecycle_with_gates_locks_and_defect_loop() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.stage_graphs["sdlc-full"].spec
    stages = {s["id"]: s for s in spec["stages"]}
    # every stage of the lifecycle, in order.
    assert [s["id"] for s in spec["stages"]] == _LIFECYCLE

    # the three human gates across the lifecycle.
    assert stages["design"]["gate"] == "consolidated-design-approval"
    assert stages["security-review"]["gate"] == "security-review-approval"
    assert stages["deploy"]["gate"] == "deploy-approval"

    # the integration-contract locks held through design approval + the compensations.
    assert set(stages["design"]["locks"]) == {"oms-inventory", "oms-web"}
    assert stages["design"]["release_locks_on"] == "design.approved"
    assert stages["design"]["compensation"] == "oms-compensate-design"
    assert stages["build"]["compensation"] == "oms-compensate-build"

    # the build stage is the HARNESS executor showcase topology.
    assert stages["build"]["topology"] == "oms-build-harness"

    # the cross-stage defect loop is carried forward from slice 8.
    loops = {loop_["when"]: loop_["to"] for loop_ in spec["loops"]}
    assert loops == {"defect.raised": "build", "defect.fixed": "sit"}


# --------------------------------------------------------------------------------------------
# The capstone: the controller drives one requirement through the ENTIRE lifecycle to done
# --------------------------------------------------------------------------------------------


def _graph() -> StageGraph:
    ws = resolve_workspace(_WS)
    return StageGraph.from_spec(ws.stage_graphs["sdlc-full"].spec)


class _Seam:
    """A scripted run_stage seam: parks the three gated stages, completes every other stage."""

    def __init__(self) -> None:
        self.calls: list[StageRunRequest] = []

    async def __call__(self, request: StageRunRequest) -> StageRunOutcome:
        self.calls.append(request)
        if request.topology in full.GATED_TOPOLOGIES:
            return StageRunOutcome(status="parked")
        return StageRunOutcome(status="completed")


@pytest.mark.asyncio
async def test_full_lifecycle_reaches_done_with_all_stages_passed_in_order() -> None:
    seam = _Seam()
    controller = PipelineController(_graph(), seam, external_events=full.EXTERNAL_EVENTS)
    cid = "OMS-101"

    # intake -> design (parks on the multi-party design gate, holding the contract locks).
    await controller.handle_event(InboundEvent(cid, "requirement.created", "jira-1"))
    s = controller.saga(cid)
    assert s is not None
    assert s.pending_gate == "consolidated-design-approval"
    assert s.held_locks == {"oms-inventory", "oms-web"}

    # design approved -> locks released -> build (harness) -> waits on external CI.
    await controller.resolve_gate(cid, approved=True)
    assert s.held_locks == set()  # released on design.approved
    assert "design" in s.passed_stages

    # CI + the mock QA rig report -> SIT passes -> PT parks nothing, PT passes -> security gate.
    await controller.handle_event(InboundEvent(cid, "build.ready-in-qa", "ci-1"))
    await controller.handle_event(InboundEvent(cid, "sit.passed", "qa-1"))
    await controller.handle_event(InboundEvent(cid, "pt.passed", "perf-1"))
    assert s.pending_gate == "security-review-approval"

    # security sign-off -> deploy packaging parks on the final release gate.
    await controller.resolve_gate(cid, approved=True)
    assert s.pending_gate == "deploy-approval"

    # eng-manager + cio sign off -> support-handover runs -> the saga is DONE.
    await controller.resolve_gate(cid, approved=True)

    saga = controller.saga(cid)
    assert saga is not None and saga.status == "done"
    assert saga.passed_stages == _LIFECYCLE
    # one bounded run per stage, every run correlated by the same id (the DORA / audit view).
    assert [r.stage_id for r in seam.calls] == _LIFECYCLE
    assert all(r.correlation_id == cid for r in seam.calls)
    assert all(e.correlation_id == cid for e in controller.timeline(cid))


@pytest.mark.asyncio
async def test_full_sdlc_demo_main_runs_green() -> None:
    """The shipped capstone demo runs end to end and reaches the done saga (the acceptance gate)."""
    await full.main()
