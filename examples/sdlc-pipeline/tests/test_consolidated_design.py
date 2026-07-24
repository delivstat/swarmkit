"""Slice 6 — consolidated design across three apps + the architect-reviewer harness review.

Covers design/details/sdlc-pipeline-example.md (build-order item 6) and gate-funnel.md layer 3:

  - the multi-app ``consolidated-design`` topology resolves + compiles (three per-app designers
    feeding the integration-architect synthesizer, which carries the four-layer funnel);
  - every role the ``consolidated-design-approval`` funnel requires resolves against the (now
    complete, incl. mobile-lead) role registry and confers the scope it is asked to approve;
  - the demo's key outcomes: consolidation happened, four distinct approvers sign off, and a
    HIGH-severity harness finding routes back before a revision passes.

The example ships under ``examples/sdlc-pipeline`` (not an installed runtime feature), so the
directory is put on ``sys.path`` to import the demo module — same as the controller tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from swarmkit_runtime.governance._approval import (
    ApprovalPolicy,
    GateStatus,
    Resolution,
    evaluate,
    tasks,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers._mock import MockModelProvider
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review._multiparty import role_task_item_id

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "examples" / "sdlc-pipeline"
_WS = _EXAMPLE / "workspace"
if str(_EXAMPLE) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE))

import demo_consolidated_design as demo  # type: ignore[import-not-found]  # noqa: E402

# --------------------------------------------------------------------------------------------
# Topology: resolves + compiles, with the funnel bound to the synthesizer
# --------------------------------------------------------------------------------------------


def test_consolidated_design_topology_resolves_and_compiles() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["consolidated-design"]
    children = {c.id: c for c in topo.root.children}

    # three per-app designers (each IAM-scoped to its own app) + the integration synthesizer.
    assert set(children) == {
        "oms-designer",
        "web-designer",
        "mobile-designer",
        "integration-designer",
    }
    for app in ("oms", "web", "mobile"):
        agent = children[f"{app}-designer"]
        assert agent.source_archetype == "solution-architect"
        assert f"app:{app}:read" in (agent.iam or {}).get("base_scope", [])
        # each app designer is walled to its own app — it does not hold the other apps' scopes.
        for other in {"oms", "web", "mobile"} - {app}:
            assert f"app:{other}:read" not in (agent.iam or {}).get("base_scope", [])

    integ = children["integration-designer"]
    assert integ.source_archetype == "integration-architect"
    assert integ.funnel is not None and integ.funnel.id == "consolidated-design-approval"
    assert "consolidated-design-synthesis" in [s.id for s in integ.skills]
    # cross-cutting: reads across all three apps (writes only the shared artifact).
    for app in ("oms", "web", "mobile"):
        assert f"app:{app}:read" in (integ.iam or {}).get("base_scope", [])

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


# --------------------------------------------------------------------------------------------
# Funnel roles: all resolve against the (now complete) role registry and confer their scope
# --------------------------------------------------------------------------------------------


def test_consolidated_funnel_roles_all_resolve() -> None:
    ws = resolve_workspace(_WS)
    registry = ws.role_registry
    spec = ws.funnels["consolidated-design-approval"].spec

    for rule in spec["approve"]["rules"]:
        for role in rule["roles"]:
            assert registry.get(role) is not None, f"role {role} missing from the registry"
            assert registry.confers(role, rule["scope"]), (
                f"role {role} does not confer {rule['scope']}"
            )

    # mobile-lead is the gap slice 6 closed: present, holds a member, confers design:approve.
    mobile_lead = registry.get("mobile-lead")
    assert mobile_lead is not None
    assert mobile_lead.members  # has at least one human identity
    assert "design:approve" in mobile_lead.scopes


def test_four_distinct_parties_satisfy_the_gate() -> None:
    """The four required parties (three app leads + infosec) are distinct and satisfy the policy."""
    ws = resolve_workspace(_WS)
    spec = ws.funnels["consolidated-design-approval"].spec
    policy = ApprovalPolicy.from_dict(spec["approve"])

    resolutions = [
        Resolution(
            identity=identity,
            role=role,
            scope="design:approve" if rule == 0 else "security:approve",
        )
        for rule, role, identity in demo.APPROVERS
    ]
    ev = evaluate(policy, ws.role_registry, resolutions, author="integration-designer")
    assert ev.status is GateStatus.APPROVED
    assert ev.distinct_approvers == frozenset({"alice", "bob", "carol", "dana"})
    assert len(ev.distinct_approvers) >= (policy.min_distinct_approvers or 0)


def test_role_task_ids_cover_every_required_party() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.funnels["consolidated-design-approval"].spec
    policy = ApprovalPolicy.from_dict(spec["approve"])

    task_roles = {t.role for t in tasks(policy)}
    assert task_roles == {"oms-lead", "web-lead", "mobile-lead", "infosec-lead"}
    # the demo seeds a resolution for each required role-task, by its deterministic item id.
    seeded = {role_task_item_id("g", ri, role) for ri, role, _ in demo.APPROVERS}
    expected = {role_task_item_id("g", t.rule_index, t.role) for t in tasks(policy)}
    assert seeded == expected


# --------------------------------------------------------------------------------------------
# Demo outcomes: consolidation, four approvers, route-back on HIGH severity
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_clean_consolidation_and_four_approvers() -> None:
    ws = demo._workspace()
    designs = await demo.run_app_designs(ws)
    assert len(designs) == 3  # one first-draft design per app

    run = await demo.run_consolidated_funnel(
        ws, designs, review_script=["clean"], correlation_id="TEST-1"
    )
    assert run.outcome == "approved"
    assert run.retries == 0  # a clean review advances with no route-back
    assert run.approvers == frozenset({"alice", "bob", "carol", "dana"})
    # consolidation happened: the single artifact reconciles all three app drafts.
    for design_id in designs:
        assert design_id in run.artifact


@pytest.mark.asyncio
async def test_demo_route_back_on_high_severity_then_passes() -> None:
    ws = demo._workspace()
    designs = await demo.run_app_designs(ws)

    run = await demo.run_consolidated_funnel(
        ws, designs, review_script=["high", "clean"], correlation_id="TEST-2"
    )
    # the HIGH finding routed back once (a revision), then the revision cleared review + approval.
    assert run.retries == 1
    assert "(revised)" in run.artifact
    assert run.outcome == "approved"
    assert len(run.approvers) == 4
