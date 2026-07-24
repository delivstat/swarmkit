"""StageRunner — bounded stage sequencing, funnel gating, IAM scoping (slice 4)."""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from swarmkit_runtime.governance import DecisionSkillResult, PolicyDecision
from swarmkit_runtime.governance._approval import ApprovalPolicy, Role, RoleRegistry
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.langgraph_compiler._stage_runner import StageRunner
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedFunnel, resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import open_gate, role_task_item_id
from swarmkit_schema.models import SwarmKitFunnel

_EXAMPLE_WS = Path(__file__).resolve().parents[3] / "examples" / "hello-swarm" / "workspace"

pytestmark = pytest.mark.asyncio

REGISTRY = RoleRegistry(
    roles={
        "oms-lead": Role("oms-lead", frozenset({"alice"}), frozenset({"design:approve"})),
        "web-lead": Role("web-lead", frozenset({"bob"}), frozenset({"design:approve"})),
    }
)

_FUNNEL_DOC: dict[str, Any] = {
    "apiVersion": "swarmkit/v1",
    "kind": "Funnel",
    "metadata": {"id": "oms-design-gate", "name": "OMS Design Gate", "description": "x" * 12},
    "judge": {"skill": "artifact-judge", "threshold": 0.8, "max_retries": 2},
    "approve": {
        "rules": [{"scope": "design:approve", "roles": ["oms-lead", "web-lead"], "quorum": "all"}],
    },
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}


def _funnel() -> ResolvedFunnel:
    return ResolvedFunnel(
        id="oms-design-gate",
        raw=SwarmKitFunnel.model_validate(_FUNNEL_DOC),
        source_path=Path("funnels/oms-design-gate.yaml"),
        spec=_FUNNEL_DOC,
    )


def _agent(
    agent_id: str, *, funnel: ResolvedFunnel | None = None, iam: dict[str, Any] | None = None
) -> ResolvedAgent:
    return ResolvedAgent(
        id=agent_id,
        role="worker",
        model=None,
        prompt=None,
        skills=(),
        iam=iam,
        funnel=funnel,
    )


def _seed_gate(
    queue: FileReviewQueue, correlation_id: str, agent_id: str, *outcomes: tuple[str, str, str]
) -> None:
    gate = f"{correlation_id}:{agent_id}"
    open_gate(
        queue,
        gate_id=gate,
        topology_id=correlation_id,
        agent_id=agent_id,
        policy=ApprovalPolicy.from_dict(_FUNNEL_DOC["approve"]),
    )
    for role, identity, decision in outcomes:
        queue.record_resolution(
            role_task_item_id(gate, 0, role),
            cast(Literal["approved", "rejected"], decision),
            identity,
        )


def _passing_governance() -> MockGovernanceProvider:
    class _Gov(MockGovernanceProvider):
        async def evaluate_decision_skill(self, **kw: Any) -> Any:
            return DecisionSkillResult(
                skill_id=kw.get("skill_id", ""), verdict="pass", confidence=0.95, reasoning="ok"
            )

    return _Gov(allow_all=True)


async def _scripted_runner(agent: ResolvedAgent, prior: str, critique: str | None) -> str:
    return f"{agent.id}-artifact(from={prior!r})"


async def test_gated_stage_run_completes_on_approval() -> None:
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        _seed_gate(
            queue,
            "req-1",
            "designer",
            ("oms-lead", "alice", "approved"),
            ("web-lead", "bob", "approved"),
        )
        runner = StageRunner(
            governance=_passing_governance(),
            review_queue=queue,
            role_registry=REGISTRY,
            agent_runner=_scripted_runner,
            max_wait_seconds=1,
        )
        result = await runner.run(
            [_agent("intake"), _agent("designer", funnel=_funnel())],
            correlation_id="req-1",
            initial_input="BRD-42",
        )
    assert result.status == "completed"
    assert result.stages[0].outcome == "produced"  # intake, ungated
    assert result.stages[1].outcome == "approved"  # designer, gated
    assert result.stages[1].gated is True


async def test_gated_stage_rejected_stops_the_run() -> None:
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        _seed_gate(
            queue,
            "req-2",
            "designer",
            ("oms-lead", "alice", "approved"),
            ("web-lead", "bob", "rejected"),
        )
        runner = StageRunner(
            governance=_passing_governance(),
            review_queue=queue,
            role_registry=REGISTRY,
            agent_runner=_scripted_runner,
            max_wait_seconds=1,
        )
        result = await runner.run(
            [_agent("designer", funnel=_funnel())], correlation_id="req-2", initial_input="BRD"
        )
    assert result.status == "rejected"
    assert result.stages[-1].outcome == "rejected"


async def test_iam_scope_denies_cross_app_stage() -> None:
    """An OMS agent that requires a web-app scope it does not hold is denied — proves scoping."""

    class _DenyingGov(MockGovernanceProvider):
        async def evaluate_action(self, **kw: Any) -> Any:
            return PolicyDecision(allowed=False, reason="out of scope", tier=0)

    oms_agent = _agent(
        "oms-writer",
        iam={"base_scope": ["app:oms:read"], "elevated_scopes": ["app:web:write"]},
    )
    runner = StageRunner(
        governance=_DenyingGov(allow_all=False),
        review_queue=None,
        role_registry=REGISTRY,
        agent_runner=_scripted_runner,
    )
    result = await runner.run([oms_agent], correlation_id="req-3", initial_input="BRD")
    assert result.status == "denied"
    assert result.stages[-1].outcome == "denied"


async def test_in_node_gate_embeds_in_a_live_topology_run() -> None:
    """A gated agent's node, compiled with gate deps, routes its output through the funnel."""
    ws = resolve_workspace(_EXAMPLE_WS)
    topo = ws.topologies["hello"]
    # Make the root a childless, gated leaf so the run is a single produce -> gate.
    gated_root = dataclasses.replace(topo.root, children=(), funnel=_funnel())
    gated_topo = dataclasses.replace(topo, root=gated_root)

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        _seed_gate(
            queue,
            topo.id,
            gated_root.id,
            ("oms-lead", "alice", "approved"),
            ("web-lead", "bob", "approved"),
        )
        graph = compile_topology(
            gated_topo,
            model_provider=MockModelProvider(),
            governance=_passing_governance(),
            review_queue=queue,
            role_registry=REGISTRY,
        )
        result = await graph.ainvoke({"input": "hello", "agent_results": {}})

    # Approved → the real agent output flows through, not a gate rejection.
    assert "[GATE REJECTED]" not in result.get("output", "")


def test_oms_example_resolves_and_compiles() -> None:
    """Smoke test: the SDLC example workspace resolves and the OMS stage run compiles."""
    example_ws = Path(__file__).resolve().parents[3] / "examples" / "sdlc-pipeline" / "workspace"
    ws = resolve_workspace(example_ws)
    assert "oms-design-gate" in ws.funnels
    assert {"oms-lead", "web-lead", "infosec-lead"} <= set(ws.role_registry.roles)
    topo = ws.topologies["oms-stage-run"]
    stages = topo.root.children
    assert [s.id for s in stages] == ["intake", "designer"]
    assert stages[1].funnel is not None and stages[1].funnel.id == "oms-design-gate"
    # Compiles (with the gate deps) without executing.
    graph = compile_topology(
        topo,
        model_provider=MockModelProvider(),
        governance=MockGovernanceProvider(),
        review_queue=None,
        role_registry=ws.role_registry,
    )
    assert graph is not None
