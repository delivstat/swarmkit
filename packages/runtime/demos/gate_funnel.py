"""Demo: the gate funnel (design/details/gate-funnel.md).

Compiles a Funnel artifact into its gate subgraph and runs it end-to-end — no model
calls, no API budget. Shows:

  0. the structural invariant on the COMPILED graph — the only edge to the terminal
     is from the human `approve` node; no automated layer can reach `done`.
  1. happy path — validate + judge pass, then the REAL multi-party approval engine
     (file-backed review queue, distinct human identities) signs off → APPROVED.
  2. a below-threshold judge → one bounded retry → the revision passes → the human.
  3. an always-failing judge → retries exhaust → the funnel ESCALATES to the human
     with the last critique attached (it never drops or silently advances).

Run it:

    uv run python packages/runtime/demos/gate_funnel.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from langgraph.graph import END
from swarmkit_runtime.governance._approval import ApprovalPolicy, Role, RoleRegistry
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    ApproveOutcome,
    JudgeOutcome,
    ValidateOutcome,
    build_multiparty_approver,
    compile_funnel_gate,
)
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import open_gate, role_task_item_id

FUNNEL_SPEC: dict[str, Any] = {
    "validate": {"autocorrect": True},
    "judge": {"skill": "artifact-judge", "threshold": 0.8, "max_retries": 2},
    "approve": {
        "rules": [
            {"scope": "design:approve", "roles": ["oms-lead", "web-lead"], "quorum": "all"},
        ],
    },
}

REGISTRY = RoleRegistry(
    roles={
        "oms-lead": Role("oms-lead", frozenset({"alice"}), frozenset({"design:approve"})),
        "web-lead": Role("web-lead", frozenset({"bob"}), frozenset({"design:approve"})),
    }
)


async def _drafter(state: dict[str, Any]) -> str:
    n = state.get("retries", 0)
    critique = state.get("critique")
    suffix = f" (revised: {critique})" if critique else ""
    return f"design draft v{n}{suffix}"


async def _ok_validator(artifact: str) -> ValidateOutcome:
    return ValidateOutcome(ok=True, artifact=artifact)


def _print_invariant(compiled: Any) -> None:
    end_sources = sorted({e.source for e in compiled.get_graph().edges if e.target == END})
    print(f"  structural invariant → only these nodes reach done: {end_sources}")
    assert end_sources == ["approve"], "invariant violated: an automated layer can reach done"


async def scenario_invariant_and_approval() -> None:
    print("① Happy path — validate + judge pass, real multi-party approval")

    async def passing_judge(artifact: str) -> JudgeOutcome:
        return JudgeOutcome(passed=True, score=0.92, critique="")

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        gov = MockGovernanceProvider()
        gate_id = "req-100-design"
        # Open the gate + pre-record the two lead approvals so the engine returns at once.
        open_gate(
            queue,
            gate_id=gate_id,
            topology_id="sdlc",
            agent_id="designer",
            policy=ApprovalPolicy.from_dict(FUNNEL_SPEC["approve"]),
        )
        queue.record_resolution(role_task_item_id(gate_id, 0, "oms-lead"), "approved", "alice")
        queue.record_resolution(role_task_item_id(gate_id, 0, "web-lead"), "approved", "bob")

        approver = build_multiparty_approver(
            FUNNEL_SPEC,
            governance=gov,
            review_queue=queue,
            registry=REGISTRY,
            topology_id="sdlc",
            agent_id="designer",
            gate_id=gate_id,
            max_wait_seconds=1,
        )
        compiled = compile_funnel_gate(
            FUNNEL_SPEC,
            drafter=_drafter,
            approver=approver,
            validator=_ok_validator,
            judge=passing_judge,
        )
        _print_invariant(compiled)
        result = await compiled.ainvoke({"artifact": "", "retries": 0})
        print(f"  outcome={result['outcome'].upper()}  retries={result['retries']}")
        print(f"  audit events: {[e.event_type for e in gov.events]}")


async def scenario_retry_then_pass() -> None:
    print("\n② Below-threshold judge → one bounded retry → the revision passes")
    calls = {"n": 0}

    async def flaky_judge(artifact: str) -> JudgeOutcome:
        calls["n"] += 1
        if calls["n"] == 1:
            return JudgeOutcome(passed=False, score=0.5, critique="tighten the data model")
        return JudgeOutcome(passed=True, score=0.88, critique="")

    async def auto_approve(state: dict[str, Any]) -> ApproveOutcome:
        return ApproveOutcome(approved=True, detail="signed off")

    compiled = compile_funnel_gate(
        FUNNEL_SPEC, drafter=_drafter, approver=auto_approve, judge=flaky_judge
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    print(f"  retries={result['retries']}  judge_passed={result['judge']['passed']}  ")
    print(f"  human saw artifact: {result['provenance']['artifact']!r}")
    print(f"  outcome={result['outcome'].upper()}")


async def scenario_exhaustion_escalates() -> None:
    print("\n③ Always-failing judge → retries exhaust → ESCALATE to the human (never dropped)")

    async def failing_judge(artifact: str) -> JudgeOutcome:
        return JudgeOutcome(passed=False, score=0.2, critique="requirements still ambiguous")

    async def human_decides(state: dict[str, Any]) -> ApproveOutcome:
        prov = state["provenance"]
        print(
            f"  escalated={prov['escalated']}  retries={prov['retries']}  "
            f"last critique={prov['critique']!r}"
        )
        return ApproveOutcome(approved=False, detail="human rejected — needs rescoping")

    compiled = compile_funnel_gate(
        FUNNEL_SPEC, drafter=_drafter, approver=human_decides, judge=failing_judge
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    print(f"  outcome={result['outcome'].upper()} (reached a human, did not silently pass or drop)")


async def main() -> None:
    await scenario_invariant_and_approval()
    await scenario_retry_then_pass()
    await scenario_exhaustion_escalates()
    print("\n✓ gate-funnel demo complete")


if __name__ == "__main__":
    asyncio.run(main())
