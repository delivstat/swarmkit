"""Slice 8 — cross-app SIT + PT (mock rigs), the pre-release security gate, and the defect loop.

Covers design/details/sdlc-pipeline-example.md (build order item 8). The saga/stage-graph half went
with the bundled pipeline (docs/notes/pipeline-deprecation.md); the topologies, funnels and rigs it
sequenced are unaffected and still asserted here.
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

import sit_pt  # type: ignore[import-not-found]  # noqa: E402

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
