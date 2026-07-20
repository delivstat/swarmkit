"""Gate funnel — compile a Funnel artifact into a LangGraph gate subgraph.

A funnel (design/details/gate-funnel.md) chains, in fixed order:

    draft -> validate -> judge -> (review) -> approve

with a bounded retry loop back to the drafter and a structural invariant: the
automated layers (validate / judge / review) *filter* and drive retries but
**never decide** — the only edge to the terminal is through the human ``approve``
layer, and retry exhaustion *escalates* to that same human (never drops, never
silently advances). The control flow is compiler-owned; a funnel configures the
layers, it does not rewire the graph.

The layer behaviours are injected as callables so the subgraph is independently
testable (fakes in tests; production adapters — approve via ``resolve_multiparty``
in :func:`build_multiparty_approver`, judge via the governance decision skill,
validate via output governance). The invariant is asserted on the *compiled graph
shape*, not on prompts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

_DEFAULT_MAX_RETRIES = 2
_DEFAULT_THRESHOLD = 0.8


class FunnelGateState(TypedDict, total=False):
    """Run state threaded through a funnel gate.

    ``artifact`` is the current draft; ``retries`` counts revisions; ``critique``
    is the failing layer's feedback carried back to the drafter; ``escalated`` is
    set when retries exhaust; ``outcome`` is the terminal human decision; and
    ``provenance`` is the bundle the human sees.
    """

    artifact: str
    retries: int
    critique: str | None
    escalated: bool
    outcome: str
    provenance: dict[str, Any]
    # Per-layer results (also folded into the provenance bundle).
    validate_ok: bool
    judge: dict[str, Any]
    review: dict[str, Any]
    approve_detail: str


@dataclass(frozen=True)
class ValidateOutcome:
    ok: bool
    artifact: str
    detail: str = ""


@dataclass(frozen=True)
class JudgeOutcome:
    passed: bool
    score: float
    critique: str = ""


@dataclass(frozen=True)
class ReviewOutcome:
    route_back: bool
    findings: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class ApproveOutcome:
    approved: bool
    detail: str = ""


# Injected layer behaviours. The state-receiving layers (draft, approve) take the
# funnel state bag as ``Any`` — it is a dynamic mapping (approve additionally sees a
# merged ``provenance`` key), so pinning it to the TypedDict would only fight callers.
Drafter = Callable[[Any], Awaitable[str]]
Validator = Callable[[str], Awaitable[ValidateOutcome]]
Judge = Callable[[str], Awaitable[JudgeOutcome]]
Reviewer = Callable[[str], Awaitable[ReviewOutcome]]
Approver = Callable[[Any], Awaitable[ApproveOutcome]]


def _max_retries(spec: dict[str, Any]) -> int:
    judge = spec.get("judge") or {}
    value = judge.get("max_retries", _DEFAULT_MAX_RETRIES)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RETRIES


def compile_funnel_gate(
    spec: dict[str, Any],
    *,
    drafter: Drafter,
    approver: Approver,
    validator: Validator | None = None,
    judge: Judge | None = None,
    reviewer: Reviewer | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compile a funnel ``spec`` (the schema-validated mapping) into a gate subgraph.

    A layer runs only if the spec declares it *and* its callable is provided;
    ``approve`` (and ``approver``) are always required. The returned graph routes
    ``draft -> [present advisory layers] -> approve -> END``, with every advisory
    failure and every retry-exhaustion routing to ``approve`` — never to ``END``.
    """
    max_retries = _max_retries(spec)

    # Which advisory layers are active (declared in spec AND wired).
    advisory: list[str] = []
    if "validate" in spec and validator is not None:
        advisory.append("validate")
    if "judge" in spec and judge is not None:
        advisory.append("judge")
    if "review" in spec and reviewer is not None:
        advisory.append("review")

    graph: StateGraph[Any] = StateGraph(FunnelGateState)

    async def draft_node(state: FunnelGateState) -> dict[str, Any]:
        artifact = await drafter(state)
        return {"artifact": artifact}

    graph.add_node("draft", draft_node)

    if validator is not None and "validate" in spec:

        async def validate_node(state: FunnelGateState) -> dict[str, Any]:
            out = await validator(state.get("artifact", ""))
            if out.ok:
                return {"artifact": out.artifact, "validate_ok": True}
            return {"validate_ok": False, "critique": out.detail}

        graph.add_node("validate", validate_node)

    if judge is not None and "judge" in spec:

        async def judge_node(state: FunnelGateState) -> dict[str, Any]:
            out = await judge(state.get("artifact", ""))
            result = {"passed": out.passed, "score": out.score, "critique": out.critique}
            patch: dict[str, Any] = {"judge": result}
            if not out.passed:
                patch["critique"] = out.critique
            return patch

        graph.add_node("judge", judge_node)

    if reviewer is not None and "review" in spec:

        async def review_node(state: FunnelGateState) -> dict[str, Any]:
            out = await reviewer(state.get("artifact", ""))
            result = {"route_back": out.route_back, "findings": out.findings}
            patch: dict[str, Any] = {"review": result}
            if out.route_back:
                patch["critique"] = out.detail
            return patch

        graph.add_node("review", review_node)

    async def approve_node(state: FunnelGateState) -> dict[str, Any]:
        provenance = {
            "artifact": state.get("artifact"),
            "judge": state.get("judge"),
            "review": state.get("review"),
            "retries": state.get("retries", 0),
            "escalated": state.get("escalated", False),
            "critique": state.get("critique"),
        }
        out = await approver({**state, "provenance": provenance})
        return {
            "outcome": "approved" if out.approved else "rejected",
            "provenance": provenance,
            "approve_detail": out.detail,
        }

    graph.add_node("approve", approve_node)

    # The revise node exists only when there is something that can fail.
    if advisory:

        async def revise_node(state: FunnelGateState) -> dict[str, Any]:
            retries = state.get("retries", 0) + 1
            if retries > max_retries:
                return {"retries": retries, "escalated": True}
            return {"retries": retries}

        graph.add_node("revise", revise_node)

    # Edges. The stage order is fixed: advisory layers (in canonical order) then approve.
    stages = [*advisory, "approve"]
    graph.add_edge(START, "draft")
    graph.add_edge("draft", stages[0])

    for stage, nxt in pairwise(stages):
        graph.add_conditional_edges(stage, _make_router(stage, nxt), {nxt: nxt, "revise": "revise"})

    # approve is the SOLE predecessor of END — the structural invariant.
    graph.add_edge("approve", END)

    if advisory:
        graph.add_conditional_edges(
            "revise", _route_revise, {"draft": "draft", "approve": "approve"}
        )

    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()


def _make_router(stage: str, nxt: str) -> Callable[[FunnelGateState], str]:
    """Advance to the next stage, or route to ``revise`` on this layer's failure."""

    def route(state: FunnelGateState) -> str:
        if stage == "validate":
            return nxt if state.get("validate_ok") else "revise"
        if stage == "judge":
            return nxt if (state.get("judge") or {}).get("passed") else "revise"
        if stage == "review":
            return "revise" if (state.get("review") or {}).get("route_back") else nxt
        return nxt

    return route


def _route_revise(state: FunnelGateState) -> str:
    """Retry the drafter while budget remains; on exhaustion escalate to the human."""
    return "approve" if state.get("escalated") else "draft"


def build_multiparty_approver(
    spec: dict[str, Any],
    *,
    governance: Any,
    review_queue: Any,
    registry: Any,
    topology_id: str,
    agent_id: str,
    gate_id: str,
    author: str | None = None,
    **resolve_kwargs: Any,
) -> Approver:
    """Bind the funnel's ``approve`` layer to the real multi-party approval engine.

    Maps the funnel's ``approve`` block onto an :class:`ApprovalPolicy` and drives
    :func:`resolve_multiparty` — the same park-and-poll engine gates use elsewhere.
    Extra ``resolve_kwargs`` (``max_wait_seconds``, ``clock``, ``poll_interval``,
    ``sleep``) are forwarded, which keeps it injectable for tests and demos.
    """
    from swarmkit_runtime.governance._approval import ApprovalPolicy  # noqa: PLC0415
    from swarmkit_runtime.review._multiparty import resolve_multiparty  # noqa: PLC0415

    policy = ApprovalPolicy.from_dict(spec["approve"])

    async def approver(state: FunnelGateState) -> ApproveOutcome:
        decision = await resolve_multiparty(
            gate_id=gate_id,
            policy=policy,
            registry=registry,
            topology_id=topology_id,
            agent_id=agent_id,
            governance=governance,
            review_queue=review_queue,
            author=author,
            **resolve_kwargs,
        )
        return ApproveOutcome(approved=decision.approved, detail=decision.reason or "")

    return approver


def build_decision_judge(spec: dict[str, Any], *, governance: Any, agent_id: str) -> Judge | None:
    """Bind the funnel's ``judge`` layer to the governance decision-skill seam.

    Returns a :class:`Judge` that scores an artifact with the funnel's ``judge.skill``
    (an audited decision skill) and passes when the verdict is ``pass`` *and* the
    confidence clears ``judge.threshold``. Returns ``None`` when there is no judge layer.
    """
    judge_cfg = spec.get("judge")
    if not judge_cfg:
        return None
    skill_id = str(judge_cfg["skill"])
    threshold = float(judge_cfg.get("threshold", _DEFAULT_THRESHOLD))

    async def judge(artifact: str) -> JudgeOutcome:
        result = await governance.evaluate_decision_skill(
            skill_id=skill_id,
            trigger="post_output",
            agent_id=agent_id,
            content=artifact,
        )
        passed = result.verdict == "pass" and result.confidence >= threshold
        return JudgeOutcome(passed=passed, score=result.confidence, critique=result.reasoning)

    return judge


async def run_agent_funnel_gate(
    funnel_spec: dict[str, Any],
    *,
    produce: Callable[[str | None], Awaitable[str]],
    governance: Any,
    review_queue: Any,
    role_registry: Any,
    topology_id: str,
    agent_id: str,
    gate_id: str | None = None,
    author: str | None = None,
    initial_artifact: str = "",
    **resolve_kwargs: Any,
) -> FunnelGateState:
    """Run a funnel gate around an agent's production and return the terminal state.

    Shared by both funnel bindings (the in-node gate in the compiler and the
    :class:`StageRunner`). ``produce(critique)`` runs the agent to draft/revise the
    artifact (the drafter; ``critique`` is ``None`` on the first pass). The judge is the
    funnel's decision skill (audited); the approve layer is the real multi-party engine.
    Returns the compiled gate's final ``FunnelGateState`` (``outcome`` + ``provenance``).
    """
    gate = gate_id or f"{topology_id}:{agent_id}"

    async def drafter(state: FunnelGateState) -> str:
        return await produce(state.get("critique"))

    judge = build_decision_judge(funnel_spec, governance=governance, agent_id=agent_id)
    approver = build_multiparty_approver(
        funnel_spec,
        governance=governance,
        review_queue=review_queue,
        registry=role_registry,
        topology_id=topology_id,
        agent_id=agent_id,
        gate_id=gate,
        author=author,
        **resolve_kwargs,
    )
    compiled = compile_funnel_gate(funnel_spec, drafter=drafter, approver=approver, judge=judge)
    result = await compiled.ainvoke({"artifact": initial_artifact, "retries": 0})
    return cast(FunnelGateState, result)
