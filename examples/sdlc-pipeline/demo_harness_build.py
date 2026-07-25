"""Demo: the harness build — the executor showcase (slice 7 of sdlc-pipeline-example.md).

The ``developer`` archetype is a **harness executor** (``executor: {kind: harness, ref:
claude-code}``). In a sandbox scoped to the OMS repo it implements the approved design and
produces a **candidate diff**; that diff faces the ``oms-code-review`` funnel — the ``code-review``
decision skill judges it, and a below-threshold verdict routes the critique back to the harness for
a bounded revision before the OMS lead signs off (the human approval is the only exit).

Deterministic — no keys, no network, no real harness, no live server. The harness seam is the one
in ``harness_build.py``: the *real* bundled ``claude-code`` declarative adapter translates a
scripted ``stream-json`` transcript into normalized ``ExecEvent``s; only the subprocess launch is
faked (the documented ``DeclarativeExecutor._open_stream`` test seam). The code-review gate is the
same machinery slice 6 used: ``compile_funnel_gate`` with a scripted judge + the real
``resolve_multiparty`` engine over a file-backed review queue seeded with the OMS lead's sign-off.

Two scenarios:

  ① Clean: the harness gets it right first time → the code-review skill passes → the OMS lead
     approves → advance, retries=0.
  ② Route-back: the first diff has an unguarded lookup → the code-review skill returns
     ``needs-changes`` → the critique routes back to the harness, which revises → the revision
     passes → the OMS lead approves, retries=1.

Run it:

    uv run python examples/sdlc-pipeline/demo_harness_build.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    JudgeOutcome,
    build_decision_judge,
    build_multiparty_approver,
    compile_funnel_gate,
)
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions, open_gate, role_task_item_id

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_build as hb

WS = Path(__file__).resolve().parent / "workspace"

# The OMS lead who exercises the code-review sign-off (from roles/sdlc-roles.yaml).
CODE_REVIEW_APPROVER = (0, "oms-lead", "alice")


class _Gov(MockGovernanceProvider):
    """A code-review decision skill scripted pass/fail + append-only audit.

    ``pass`` clears the funnel's judge; ``needs-changes`` (below threshold) drives the route-back.
    The reasoning is the critique carried back to the developer harness.
    """

    def __init__(self, verdicts: list[bool]) -> None:
        super().__init__()
        self._verdicts = verdicts
        self._n = 0

    async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
        ok = self._verdicts[self._n] if self._n < len(self._verdicts) else True
        self._n += 1
        if ok:
            return DecisionSkillResult(
                skill_id=kw.get("skill_id", ""),
                verdict="pass",
                confidence=0.92,
                reasoning="diff implements the guarded order-status lookup; matches the design",
            )
        return DecisionSkillResult(
            skill_id=kw.get("skill_id", ""),
            verdict="needs-changes",
            confidence=0.45,
            reasoning="get_order_status does not guard unknown ids — a KeyError leaks as a 500",
        )


@dataclass
class GateRun:
    outcome: str
    retries: int
    approver: str | None
    diffs: list[hb.CandidateDiff]


def _print_candidate(diff: hb.CandidateDiff, *, label: str, resumed: bool = False) -> None:
    tools = " → ".join(diff.tool_calls)
    session = f"{diff.session_id}{' (resumed)' if resumed else ''}"
    print("   harness run (claude-code, sandbox=worktree, repo=fixtures/demo-repo, net=deny)")
    print(
        f"     · tool trail: {tools}   session={session}  "
        f"cost=${diff.cost_usd:.2f}  status={diff.status}"
    )
    manifest = f"{len(diff.manifest)} file_change(s)"
    print(f"   candidate diff ({label})  {diff.summary}  manifest={manifest}:")
    for line in diff.diff.splitlines():
        print(f"       {line}")


async def run_code_review_gate(
    ws: Any,
    *,
    verdicts: list[bool],
    clean_first: bool,
    correlation_id: str,
    verbose: bool = False,
) -> GateRun:
    """Run the developer harness under the ``oms-code-review`` funnel.

    ``clean_first`` picks the first diff the harness emits (a guarded one that clears review, or the
    flawed one that routes back). The drafter re-runs the harness on a route-back (revising with the
    critique) — the executor is *inside* the gate loop.
    """
    spec = dict(ws.funnels["oms-code-review"].spec)
    author = (
        "builder"  # the developer node authored the diff; exclude_author bars it from approving
    )
    gate_id = f"{correlation_id}:builder"
    diffs: list[hb.CandidateDiff] = []

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        policy = ApprovalPolicy.from_dict(spec["approve"])
        open_gate(
            queue, gate_id=gate_id, topology_id=correlation_id, agent_id="builder", policy=policy
        )
        rule_index, role, identity = CODE_REVIEW_APPROVER
        queue.record_resolution(role_task_item_id(gate_id, rule_index, role), "approved", identity)

        async def drafter(state: Any) -> str:
            is_revision = state.get("critique") is not None
            revised = is_revision or clean_first
            label = f"draft#{len(diffs) + 1}{', revised' if is_revision else ''}"
            diff = await hb.run_developer_harness(
                revised=revised,
                session_id=f"sess-{correlation_id.lower()}",
                cost_usd=0.42 if not is_revision else 0.18,
            )
            diffs.append(diff)
            if verbose:
                if is_revision:
                    critique = state.get("critique")
                    print("   ↩ route-back: revising the harness with the review critique")
                    print(f'     critique fed to the harness: "{critique}"')
                _print_candidate(diff, label=label, resumed=is_revision)
            return diff.diff

        judge = build_decision_judge(spec, governance=_Gov(verdicts), agent_id="builder")
        if judge is not None and verbose:
            judge = _verbose_judge(judge)

        approver = build_multiparty_approver(
            spec,
            governance=MockGovernanceProvider(),
            review_queue=queue,
            registry=ws.role_registry,
            topology_id=correlation_id,
            agent_id="builder",
            gate_id=gate_id,
            author=author,
            max_wait_seconds=1,
        )
        compiled = compile_funnel_gate(spec, drafter=drafter, approver=approver, judge=judge)
        state = await compiled.ainvoke({"artifact": "", "retries": 0})

        collected = collect_resolutions(queue, gate_id=gate_id, policy=policy)
        ev = evaluate(policy, ws.role_registry, collected, author)

    approver_id = None
    if ev.status is GateStatus.APPROVED:
        approver_id = ", ".join(sorted(ev.distinct_approvers))
        if verbose:
            print(f"     · code-review gate → OMS lead ({approver_id}) signed off")
    provenance = state.get("provenance", {})
    return GateRun(
        outcome=str(state.get("outcome", "rejected")),
        retries=int(provenance.get("retries", 0)),
        approver=approver_id,
        diffs=diffs,
    )


def _verbose_judge(judge: Any) -> Any:
    """Wrap the decision judge to narrate its verdict (the code-review determination)."""

    async def narrated(artifact: str) -> JudgeOutcome:
        out = await judge(artifact)
        if out.passed:
            print(f"     · code-review (code-review skill) → PASS {out.score:.2f}  advance")
        else:
            print(
                f"     · code-review (code-review skill) → NEEDS-CHANGES {out.score:.2f}  "
                f'⇒ ROUTE BACK\n       finding: "{out.critique}"'
            )
        return out

    return narrated


async def scenario_clean(ws: Any) -> GateRun:
    print("① Clean build — harness → candidate diff → code-review passes → OMS lead approves")
    run = await run_code_review_gate(
        ws, verdicts=[True], clean_first=True, correlation_id="BUILD-201", verbose=True
    )
    print(f"   GATE OUTCOME: {run.outcome.upper()}  (advance)  retries={run.retries}\n")
    return run


async def scenario_route_back(ws: Any) -> GateRun:
    print(
        "② Route-back — code-review finds an unguarded lookup → back to harness → revision passes"
    )
    run = await run_code_review_gate(
        ws, verdicts=[False, True], clean_first=False, correlation_id="BUILD-202", verbose=True
    )
    print(f"   GATE OUTCOME: {run.outcome.upper()}  (advance)  retries={run.retries}\n")
    return run


async def main() -> None:
    ws = resolve_workspace(WS)
    await scenario_clean(ws)
    await scenario_route_back(ws)
    print("✓ harness-build demo complete")


if __name__ == "__main__":
    asyncio.run(main())
