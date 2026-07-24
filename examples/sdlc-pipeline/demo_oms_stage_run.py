"""Demo: the one-app (OMS) bounded stage run (design/details/sdlc-pipeline-example.md, slice 4).

Runs intake -> design -> judge -> approval as one bounded, deterministic stage run — no model
calls, no API budget. The StageRunner sequences the stages (agent-determination-only: code
sequences, agents produce); the design stage is gated by the OMS design funnel (judge -> real
multi-party approval). Shows:

  1. a clean run — intake produces, design is judged, the OMS + Web leads sign off (real
     resolve_multiparty engine + file-backed review queue) → the run COMPLETES.
  2. a below-threshold design → one bounded retry to the architect → the revision passes.
  3. IAM scoping — an OMS agent that reaches for a Web-app scope it does not hold is DENIED.

Run it:

    uv run python examples/sdlc-pipeline/demo_oms_stage_run.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime.governance import DecisionSkillResult, PolicyDecision
from swarmkit_runtime.governance._approval import ApprovalPolicy
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._stage_runner import StageRunner
from swarmkit_runtime.resolver import ResolvedAgent, resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import open_gate, role_task_item_id

WS = Path(__file__).resolve().parent / "workspace"


class _Gov(MockGovernanceProvider):
    """Passing judge + policy engine that denies a scope the agent does not hold."""

    def __init__(self, *, judge_script: list[bool] | None = None) -> None:
        super().__init__(allow_all=True)
        self._judge_script = judge_script or []
        self._n = 0

    async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
        passed = self._judge_script[self._n] if self._n < len(self._judge_script) else True
        self._n += 1
        return DecisionSkillResult(
            skill_id=kw.get("skill_id", ""),
            verdict="pass" if passed else "fail",
            confidence=0.92 if passed else 0.4,
            reasoning="meets the rubric" if passed else "tighten the OMS data model",
        )

    async def evaluate_action(self, **kw: Any) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason="out of app scope", tier=0)


def _stages() -> list[ResolvedAgent]:
    ws = resolve_workspace(WS)
    return list(ws.topologies["oms-stage-run"].root.children)


def _designer_funnel_spec() -> dict[str, Any]:
    ws = resolve_workspace(WS)
    return dict(ws.funnels["oms-design-gate"].spec)


def _roles() -> Any:
    return resolve_workspace(WS).role_registry


async def _agent_runner(agent: ResolvedAgent, prior: str, critique: str | None) -> str:
    tag = " (revised)" if critique else ""
    return f"<{agent.id} artifact{tag} from {prior[:24]!r}>"


def _seed_design_gate(queue: FileReviewQueue, req: str, *, reject: bool = False) -> None:
    gate = f"{req}:designer"
    open_gate(
        queue,
        gate_id=gate,
        topology_id=req,
        agent_id="designer",
        policy=ApprovalPolicy.from_dict(_designer_funnel_spec()["approve"]),
    )
    queue.record_resolution(role_task_item_id(gate, 0, "oms-lead"), "approved", "alice")
    queue.record_resolution(
        role_task_item_id(gate, 0, "web-lead"), "rejected" if reject else "approved", "bob"
    )


async def scenario_clean() -> None:
    print("① Clean run — intake → design → judge(pass) → OMS+Web leads approve")
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        _seed_design_gate(queue, "OMS-42")
        runner = StageRunner(
            governance=_Gov(),
            review_queue=queue,
            role_registry=_roles(),
            agent_runner=_agent_runner,
            max_wait_seconds=1,
        )
        result = await runner.run(
            _stages(), correlation_id="OMS-42", initial_input="BRD-42: add split shipment"
        )
    for s in result.stages:
        print(f"   · {s.agent_id:<9} gated={s.gated!s:<5} outcome={s.outcome}")
    print(f"   RUN STATUS: {result.status.upper()}\n")


async def scenario_retry() -> None:
    print("② Below-threshold design → one bounded retry → the revision passes")
    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        _seed_design_gate(queue, "OMS-43")
        runner = StageRunner(
            governance=_Gov(judge_script=[False, True]),  # fail once, then pass
            review_queue=queue,
            role_registry=_roles(),
            agent_runner=_agent_runner,
            max_wait_seconds=1,
        )
        result = await runner.run(_stages(), correlation_id="OMS-43", initial_input="BRD-43")
    design = result.stages[-1]
    print(f"   design retries={design.provenance['retries']}  outcome={design.outcome}")
    print(f"   RUN STATUS: {result.status.upper()}\n")


async def scenario_scope_denied() -> None:
    print("③ IAM scoping — an OMS agent reaching for a Web-app scope is denied")
    oms_over_reacher = dataclasses.replace(
        _stages()[0],
        id="oms-writer",
        funnel=None,
        iam={"base_scope": ["app:oms:read"], "elevated_scopes": ["app:web:write"]},
    )
    runner = StageRunner(
        governance=_Gov(),
        review_queue=None,
        role_registry=_roles(),
        agent_runner=_agent_runner,
    )
    result = await runner.run([oms_over_reacher], correlation_id="OMS-44", initial_input="BRD-44")
    print(f"   stage {result.stages[-1].agent_id} outcome={result.stages[-1].outcome}")
    print(f"   RUN STATUS: {result.status.upper()} (denied by IAM scope, not by prompt)\n")


async def main() -> None:
    await scenario_clean()
    await scenario_retry()
    await scenario_scope_denied()
    print("✓ OMS stage-run demo complete")


if __name__ == "__main__":
    asyncio.run(main())
