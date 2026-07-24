"""Demo: the consolidated design across all three apps + the architect-reviewer harness review.

Slice 6 of design/details/sdlc-pipeline-example.md. Deterministic — no model calls, no harness,
no API budget, no live server (every seam is faked, mirroring ``demo_oms_stage_run.py``).

The multi-app design stage:

  1. Three per-app solution architects (OMS / Web / Mobile), each IAM-scoped to its own app,
     draft a first-pass design.
  2. The integration-architect synthesises the three drafts into ONE consolidated design (the
     ``consolidated-design-synthesis`` coordination skill).
  3. That consolidated design runs the four-layer ``consolidated-design-approval`` funnel:
     deterministic ``validate`` -> ``judge`` (pass) -> the ``architect-reviewer`` **harness
     review** (layer 3, investigative) -> multi-party ``approve`` — the app leads + infosec-lead.

Two scenarios:

  ① Clean: the harness review finds nothing high-severity and advances; all four parties sign off.
  ② Route-back: the harness review surfaces a HIGH-severity finding, which routes back (per the
     funnel's ``route_back_at: high``) before any human is paged; a revision then clears review and
     the four parties sign off.

The seams are faked the same way the OMS demo fakes them:
  - model (drafting / synthesis): a plain string-returning ``agent_runner`` — no ModelProvider.
  - harness review (layer 3): a scripted reviewer callable returning clean / high findings.
  - judge (layer 2) + audit: a passing ``MockGovernanceProvider``.
  - approval (layer 4): the REAL ``resolve_multiparty`` engine over a file-backed review queue
    seeded with four distinct human resolutions (alice / bob / carol / dana).

Run it:

    uv run python examples/sdlc-pipeline/demo_consolidated_design.py
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    JudgeOutcome,
    ReviewOutcome,
    ValidateOutcome,
    build_multiparty_approver,
    compile_funnel_gate,
)
from swarmkit_runtime.resolver import ResolvedAgent, resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions, open_gate, role_task_item_id

WS = Path(__file__).resolve().parent / "workspace"

# The four distinct human approvers, keyed by the role each holds (from roles/sdlc-roles.yaml).
APPROVERS: tuple[tuple[int, str, str], ...] = (
    (0, "oms-lead", "alice"),
    (0, "web-lead", "bob"),
    (0, "mobile-lead", "carol"),
    (1, "infosec-lead", "dana"),
)


class _Gov(MockGovernanceProvider):
    """A passing judge + append-only audit (the funnel's layer-2 + audit seam)."""

    async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
        return DecisionSkillResult(
            skill_id=kw.get("skill_id", ""),
            verdict="pass",
            confidence=0.92,
            reasoning="the consolidated design meets the rubric",
        )


@dataclass(frozen=True)
class FunnelRun:
    outcome: str
    retries: int
    approvers: frozenset[str]
    artifact: str


def _workspace() -> Any:
    return resolve_workspace(WS)


def _children(ws: Any) -> list[ResolvedAgent]:
    return list(ws.topologies["consolidated-design"].root.children)


def _app_designers(ws: Any) -> list[ResolvedAgent]:
    return [
        c for c in _children(ws) if c.id.endswith("-designer") and c.id != "integration-designer"
    ]


def _integration_designer(ws: Any) -> ResolvedAgent:
    return next(c for c in _children(ws) if c.id == "integration-designer")


async def run_app_designs(ws: Any, *, verbose: bool = False) -> dict[str, str]:
    """Run the three per-app solution architects (faked) — one first-draft design each."""
    designs: dict[str, str] = {}
    for agent in _app_designers(ws):
        app = agent.iam["base_scope"][-1].split(":")[1] if agent.iam else agent.id
        designs[agent.id] = f"<{app}-design: scoped to app:{app}, by {agent.source_archetype}>"
        if verbose:
            scope = sorted(agent.iam["base_scope"]) if agent.iam else []
            print(f"   · {agent.id:<17} scope={scope!s:<52} → drafted")
    return designs


async def run_consolidated_funnel(
    ws: Any,
    app_designs: dict[str, str],
    *,
    review_script: list[str],
    correlation_id: str,
    verbose: bool = False,
) -> FunnelRun:
    """Synthesise the consolidated design and run it through the four-layer funnel.

    The funnel's *drafter* is the integration-architect synthesis: it merges the three app
    drafts (via the consolidated-design-synthesis skill) into ONE design, and on a route-back
    it revises with the reviewer's findings. ``review_script`` scripts the harness reviewer's
    findings per draft ("high" routes back; "clean" advances).
    """
    integ = _integration_designer(ws)
    spec = dict(ws.funnels["consolidated-design-approval"].spec)
    # The synthesizer authored the artifact — exclude_author bars it from approving.
    author = integ.id
    gate_id = f"{correlation_id}:{integ.id}"

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        policy = ApprovalPolicy.from_dict(spec["approve"])
        open_gate(
            queue, gate_id=gate_id, topology_id=correlation_id, agent_id=integ.id, policy=policy
        )
        for rule_index, role, identity in APPROVERS:
            item = role_task_item_id(gate_id, rule_index, role)
            queue.record_resolution(item, "approved", identity)

        drafts = {"n": 0}
        reviews = iter(review_script)

        async def drafter(state: Any) -> str:
            drafts["n"] += 1
            critique = state.get("critique")
            revised = " (revised)" if critique else ""
            skill = integ.skills[0].id if integ.skills else "consolidated-design-synthesis"
            artifact = (
                f"<consolidated-design{revised} via {skill}: reconciles "
                f"{' + '.join(sorted(app_designs))}; integration contracts pinned>"
            )
            if verbose:
                merged = ", ".join(sorted(app_designs))
                print(f"      draft#{drafts['n']}: {integ.id} consolidates [{merged}]{revised}")
            return artifact

        async def validator(artifact: str) -> ValidateOutcome:
            if verbose:
                print("      · layer 1 validate  (deterministic schema) → ok")
            return ValidateOutcome(ok=True, artifact=artifact)

        async def judge(artifact: str) -> JudgeOutcome:
            if verbose:
                print("      · layer 2 judge     (artifact-judge rubric) → pass 0.92")
            return JudgeOutcome(passed=True, score=0.92, critique="")

        async def reviewer(artifact: str) -> ReviewOutcome:
            verdict = next(reviews, "clean")
            if verdict == "high":
                if verbose:
                    print(
                        "      · layer 3 review    (architect-reviewer harness) → HIGH finding: "
                        "OMS↔Web order contract drift ⇒ ROUTE BACK (route_back_at: high)"
                    )
                return ReviewOutcome(
                    route_back=True,
                    findings=[{"severity": "high", "detail": "payload ≠ oms-web contract"}],
                    detail="High: consolidated order payload diverges from the oms-web contract.",
                )
            if verbose:
                print(
                    "      · layer 3 review    (architect-reviewer harness) → clean "
                    "(no high-severity finding) → advance to human approval"
                )
            return ReviewOutcome(
                route_back=False,
                findings=[{"severity": "low", "detail": "note: document the retry SLA"}],
            )

        gov = _Gov()
        approver = build_multiparty_approver(
            spec,
            governance=gov,
            review_queue=queue,
            registry=ws.role_registry,
            topology_id=correlation_id,
            agent_id=integ.id,
            gate_id=gate_id,
            author=author,
            max_wait_seconds=1,
        )
        compiled = compile_funnel_gate(
            spec,
            drafter=drafter,
            approver=approver,
            validator=validator,
            judge=judge,
            reviewer=reviewer,
        )
        state = await compiled.ainvoke({"artifact": "", "retries": 0})

        collected = collect_resolutions(queue, gate_id=gate_id, policy=policy)
        ev = evaluate(policy, ws.role_registry, collected, author)

    provenance = state.get("provenance", {})
    return FunnelRun(
        outcome=str(state.get("outcome", "rejected")),
        retries=int(provenance.get("retries", 0)),
        approvers=ev.distinct_approvers if ev.status is GateStatus.APPROVED else frozenset(),
        artifact=str(provenance.get("artifact") or ""),
    )


async def scenario_clean(ws: Any) -> FunnelRun:
    print("① Clean run — three app designs → consolidation → 4-layer funnel → 4-party approval")
    designs = await run_app_designs(ws, verbose=True)
    run = await run_consolidated_funnel(
        ws, designs, review_script=["clean"], correlation_id="REQ-101", verbose=True
    )
    approvers = ", ".join(sorted(run.approvers))
    print(f"   consolidated 1 design from {len(designs)} app drafts; retries={run.retries}")
    print(f"   approved by {approvers} ({len(run.approvers)} distinct approvers)")
    print(f"   FUNNEL OUTCOME: {run.outcome.upper()}\n")
    return run


async def scenario_route_back(ws: Any) -> FunnelRun:
    print("② Route-back — a HIGH-severity harness finding routes back; the revision then passes")
    designs = await run_app_designs(ws)
    run = await run_consolidated_funnel(
        ws, designs, review_script=["high", "clean"], correlation_id="REQ-102", verbose=True
    )
    approvers = ", ".join(sorted(run.approvers))
    print(f"   route-back happened: retries={run.retries} (1 revision after the HIGH finding)")
    print(f"   approved by {approvers} ({len(run.approvers)} distinct approvers)")
    print(f"   FUNNEL OUTCOME: {run.outcome.upper()}\n")
    return run


async def main() -> None:
    ws = _workspace()
    await scenario_clean(ws)
    await scenario_route_back(ws)
    print("✓ consolidated-design demo complete")


if __name__ == "__main__":
    asyncio.run(main())
