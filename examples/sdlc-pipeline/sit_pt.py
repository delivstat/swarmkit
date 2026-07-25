"""Mock rigs + the SIT / PT / security-review determination, faked deterministically (slice 8).

The SIT and PT stages run against **mock rigs** — the external QA + perf environments are mocked
here (no real systems, no network). The seam is one place so ``demo_sit_pt.py`` and the slice-8
tests share it:

  - :func:`run_sit_rig` — the cross-app e2e business flows (oms/web/mobile) the ``sit-qa`` engineer
    executes; a scripted flow can fail, which the pipeline files as a defect (the ``defect.raised``
    loop the controller drives in ``demo_defect_loop.py``).
  - :func:`run_pt_rig` + :func:`pt_analysis` — the perf samples the ``pt-engineer`` collects and the
    ``pt-analysis`` decision skill's verdict against the agreed thresholds (pass / regression). The
    verdict is computed deterministically here (a real run routes the metrics through the skill).
  - :func:`run_security_review_gate` — the pre-release ``security-review-approval`` funnel: an
    investigative ``security-consultant`` HARNESS review (compliance / SAST / DAST) whose HIGH
    finding routes back before the ``infosec-lead`` signs off. Reuses the runtime's real funnel
    machinery + the real ``resolve_multiparty`` engine over a file-backed queue (as slice 6 does).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
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
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions, open_gate, role_task_item_id

# The pre-release security sign-off — the infosec-lead (from roles/sdlc-roles.yaml).
SECURITY_APPROVER = (0, "infosec-lead", "dana")


# --------------------------------------------------------------------------------------------
# SIT mock rig: cross-app e2e business flows (frontend -> backend, across oms / web / mobile)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowResult:
    """One end-to-end business flow's result on the mock SIT rig."""

    flow: str
    apps: tuple[str, ...]
    passed: bool
    detail: str = ""


# The cross-app flows the SIT engineer runs — each threads several apps, which is why SIT reads
# across all three (the shared surface) rather than a single app in isolation.
_SIT_FLOWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("browse → add-to-cart → checkout", ("web", "oms")),
    ("mobile order → status push", ("mobile", "oms")),
    ("order → fulfil → inventory sync", ("oms", "web")),
)


def run_sit_rig(*, failing_flow: str | None = None) -> list[FlowResult]:
    """Run the cross-app e2e suite on the mock rig.

    ``failing_flow`` (a substring of a flow name) marks that flow failed — the SIT defect the
    controller-driven loop re-tests after the fix (``demo_defect_loop.py``).
    """
    results: list[FlowResult] = []
    for name, apps in _SIT_FLOWS:
        failed = failing_flow is not None and failing_flow in name
        detail = "assertion: order state ≠ 'confirmed' after checkout" if failed else ""
        results.append(FlowResult(flow=name, apps=apps, passed=not failed, detail=detail))
    return results


# --------------------------------------------------------------------------------------------
# PT mock rig + the pt-analysis decision (metrics vs thresholds)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PerfSample:
    """One exposed service's perf sample from the mock PT rig."""

    service: str
    p95_ms: float
    error_rate: float


@dataclass(frozen=True)
class PtVerdict:
    """The ``pt-analysis`` decision skill's output (skills/pt-analysis.yaml)."""

    verdict: str  # pass | fail
    breaches: tuple[str, ...] = ()
    reasoning: str = ""


# The agreed PT thresholds (the rig's SLOs) the pt-analysis skill judges against.
PT_THRESHOLDS = {"p95_ms": 400.0, "error_rate": 0.01}


def run_pt_rig(*, regression: bool = False) -> list[PerfSample]:
    """Collect perf samples of the exposed services on the mock rig.

    ``regression=True`` injects a latency regression on the order API (a breach of the p95 SLO).
    """
    order_p95 = 720.0 if regression else 240.0
    return [
        PerfSample(service="oms.order-api", p95_ms=order_p95, error_rate=0.004),
        PerfSample(service="web.catalog-api", p95_ms=180.0, error_rate=0.002),
        PerfSample(service="mobile.gateway", p95_ms=210.0, error_rate=0.003),
    ]


def pt_analysis(samples: list[PerfSample]) -> PtVerdict:
    """Judge the perf samples against the agreed thresholds (the ``pt-analysis`` decision).

    Deterministic here (threshold arithmetic); a live run routes the metrics + thresholds through
    the LLM decision skill. Cross-app regression is included: any service breaching fails the gate.
    """
    breaches: list[str] = []
    for s in samples:
        if s.p95_ms > PT_THRESHOLDS["p95_ms"]:
            breaches.append(f"{s.service} p95 {s.p95_ms:.0f}ms > {PT_THRESHOLDS['p95_ms']:.0f}ms")
        if s.error_rate > PT_THRESHOLDS["error_rate"]:
            breaches.append(
                f"{s.service} error-rate {s.error_rate:.3f} > {PT_THRESHOLDS['error_rate']:.3f}"
            )
    if breaches:
        return PtVerdict(
            verdict="fail",
            breaches=tuple(breaches),
            reasoning="one or more exposed services breached the agreed thresholds",
        )
    return PtVerdict(
        verdict="pass",
        reasoning="all exposed services within latency/error thresholds; no cross-app regression",
    )


# --------------------------------------------------------------------------------------------
# The pre-release security-review funnel (security-consultant harness review + infosec sign-off)
# --------------------------------------------------------------------------------------------


class _SecurityJudge(MockGovernanceProvider):
    """A passing artifact-judge + append-only audit (the funnel's layer-2 + audit seam)."""

    async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
        return DecisionSkillResult(
            skill_id=kw.get("skill_id", ""),
            verdict="pass",
            confidence=0.9,
            reasoning="the release package meets the security rubric",
        )


@dataclass(frozen=True)
class SecurityReviewRun:
    outcome: str
    retries: int
    approvers: frozenset[str] = field(default_factory=frozenset)
    findings: list[dict[str, Any]] = field(default_factory=list)


async def run_security_review_gate(
    ws: Any,
    *,
    review_script: list[str],
    correlation_id: str,
    verbose: bool = False,
) -> SecurityReviewRun:
    """Run the release package through the ``security-review-approval`` funnel.

    ``review_script`` scripts the ``security-consultant`` harness review's findings per draft:
    ``"high"`` routes back (per ``route_back_at: high``) before the infosec-lead is paged;
    ``"clean"`` advances. Reuses the real funnel machinery + the real multi-party engine.
    """
    spec = dict(ws.funnels["security-review-approval"].spec)
    author = "release-gate"  # the release-coordinator authored the package; exclude_author bars it
    gate_id = f"{correlation_id}:release-gate"
    last_findings: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        policy = ApprovalPolicy.from_dict(spec["approve"])
        open_gate(
            queue,
            gate_id=gate_id,
            topology_id=correlation_id,
            agent_id="release-gate",
            policy=policy,
        )
        rule_index, role, identity = SECURITY_APPROVER
        queue.record_resolution(role_task_item_id(gate_id, rule_index, role), "approved", identity)

        drafts = {"n": 0}
        reviews = iter(review_script)

        async def drafter(state: Any) -> str:
            drafts["n"] += 1
            revised = " (revised)" if state.get("critique") else ""
            if verbose:
                print(
                    f"      draft#{drafts['n']}: release-coordinator assembles the package{revised}"
                )
            return f"<release-package{revised}: design + diffs + SIT/PT results>"

        async def validator(artifact: str) -> ValidateOutcome:
            if verbose:
                print("      · layer 1 validate  (deterministic schema) → ok")
            return ValidateOutcome(ok=True, artifact=artifact)

        async def judge(artifact: str) -> JudgeOutcome:
            if verbose:
                print("      · layer 2 judge     (artifact-judge rubric) → pass 0.90")
            return JudgeOutcome(passed=True, score=0.9, critique="")

        async def reviewer(artifact: str) -> ReviewOutcome:
            nonlocal last_findings
            verdict = next(reviews, "clean")
            if verdict == "high":
                last_findings = [
                    {"severity": "high", "detail": "PII order export lacks data-residency scoping"}
                ]
                if verbose:
                    print(
                        "      · layer 3 review    (security-consultant harness) → HIGH finding: "
                        "PII residency gap ⇒ ROUTE BACK (route_back_at: high)"
                    )
                return ReviewOutcome(
                    route_back=True,
                    findings=last_findings,
                    detail="High: order export bypasses the data-residency boundary.",
                )
            last_findings = [{"severity": "low", "detail": "note: rotate the SAST baseline"}]
            if verbose:
                print(
                    "      · layer 3 review    (security-consultant harness) → clean "
                    "(no high-severity finding) → advance to infosec sign-off"
                )
            return ReviewOutcome(route_back=False, findings=last_findings)

        approver = build_multiparty_approver(
            spec,
            governance=_SecurityJudge(),
            review_queue=queue,
            registry=ws.role_registry,
            topology_id=correlation_id,
            agent_id="release-gate",
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
    return SecurityReviewRun(
        outcome=str(state.get("outcome", "rejected")),
        retries=int(provenance.get("retries", 0)),
        approvers=ev.distinct_approvers if ev.status is GateStatus.APPROVED else frozenset(),
        findings=last_findings,
    )
