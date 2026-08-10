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

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger("swarmkit.funnels")

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
    # The gated node's produced diff (a harness executor), threaded so deterministic validate
    # layers can enforce on the *change* rather than the artifact string. None ⇒ no diff surfaced
    # (a model node, or a harness with no worktree change); refreshed on every draft/revision.
    diff: str | None
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
class ValidateContext:
    """What the deterministic validate layer sees: the current ``artifact`` and, when the gated
    node produced one, the ``diff`` threaded from the executor. ``slice_budget`` enforces on the
    diff; ``cited_change`` reads the artifact as a change-rationale and resolves it against the
    diff. New fields are additive — a validator that only needs the artifact ignores ``diff``.
    """

    artifact: str
    diff: str | None = None


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
Validator = Callable[[ValidateContext], Awaitable[ValidateOutcome]]
Judge = Callable[[str], Awaitable[JudgeOutcome]]
Reviewer = Callable[[str], Awaitable[ReviewOutcome]]
Approver = Callable[[Any], Awaitable[ApproveOutcome]]
# Reads the diff produced by the most recent draft (the executor's worktree change), or None.
# Injected alongside the drafter so ``draft_node`` can refresh ``diff`` on every (re)draft.
DiffSource = Callable[[], str | None]


def _max_retries(spec: dict[str, Any]) -> int:
    judge = spec.get("judge") or {}
    value = judge.get("max_retries", _DEFAULT_MAX_RETRIES)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RETRIES


def _draft_patch(artifact: str, diff_source: DiffSource | None) -> dict[str, Any]:
    """The draft node's state patch: the artifact plus, when a ``diff_source`` is wired, the diff
    this (re)draft produced — refreshed each pass so validate always sees the current change."""
    patch: dict[str, Any] = {"artifact": artifact}
    if diff_source is not None:
        patch["diff"] = diff_source()
    return patch


def compile_funnel_gate(
    spec: dict[str, Any],
    *,
    drafter: Drafter,
    approver: Approver,
    validator: Validator | None = None,
    judge: Judge | None = None,
    reviewer: Reviewer | None = None,
    diff_source: DiffSource | None = None,
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
        return _draft_patch(await drafter(state), diff_source)

    graph.add_node("draft", draft_node)

    if validator is not None and "validate" in spec:

        async def validate_node(state: FunnelGateState) -> dict[str, Any]:
            out = await validator(
                ValidateContext(artifact=state.get("artifact", ""), diff=state.get("diff"))
            )
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
            # `validate_ok` was the one layer result the bundle omitted, so a reader of the
            # provenance could not tell a passed schema check from an absent one.
            "validate_ok": state.get("validate_ok", True),
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


def _resolve_validate_schema(
    validate_cfg: Mapping[str, Any],
    declared_in: Path | None,
    workspace_root: Path | None,
) -> dict[str, Any] | None:
    """Resolve ``validate.schema`` — inline object or a path — to a parsed JSON Schema.

    Paths resolve against the FUNNEL that declared them first, matching how ``output_schema``
    resolves against the artifact that declared it — then, failing that, against the WORKSPACE ROOT.

    The fallback is not cosmetic. ``schemas/`` is a workspace-level directory beside ``funnels/``,
    so ``schema: schemas/spec-review.schema.json`` in a funnel is the spelling every author reaches
    for, and it is what the reference workspace and the reporter's own funnel both use. Without the
    fallback the layer resolves nothing and the funnel is fixed but still inert — the same defect
    one level down. The declaring-artifact rule stays first, so nothing that resolves today changes
    meaning; no real layout has a ``funnels/schemas/`` directory for the two to disagree about.

    A schema that resolves to neither is a configuration error the operator has to see, not a
    validate layer that quietly disappears: it warns and returns None, so the rest of the funnel
    still runs.
    """
    raw = validate_cfg.get("schema")
    if raw is None or not isinstance(raw, (dict, str)):
        return None
    from swarmkit_runtime.resolver._output_schema_ref import (  # noqa: PLC0415
        OutputSchemaError,
        resolve_output_schema,
    )

    bases = [declared_in or Path.cwd()]
    if isinstance(raw, str) and workspace_root is not None:
        bases.append(Path(workspace_root))
    first_error: Exception | None = None
    for base in bases:
        try:
            resolved = resolve_output_schema(raw, declared_in=base, workspace_root=workspace_root)
        except (OutputSchemaError, OSError) as exc:
            first_error = first_error or exc
            continue
        return dict(resolved) if resolved else None

    logger.warning(
        "funnel `validate.schema` %r could not be read (%s) — the schema check will not run. "
        "The rest of the funnel is unaffected.",
        raw,
        first_error,
    )
    return None


def _schema_verdict(artifact: str, schema: dict[str, Any]) -> str:
    """The validate layer's critique for a schema failure, or ``""`` when the artifact conforms.

    Parsing is tolerant (``extract_json_object``) because a model that fences its JSON has produced
    a conforming artifact badly presented, not a non-conforming one — and this critique goes back to
    the drafter as a retry instruction, where "not valid JSON" would send it to fix the wrong thing.
    """
    from swarmkit_runtime.skills._output_validator import (  # noqa: PLC0415
        extract_json_object,
        validate_all_skill_output,
    )

    parsed = extract_json_object(artifact)
    if parsed is None:
        return (
            "the artifact is not a JSON object, so it cannot be checked against `validate.schema`"
        )
    errors = validate_all_skill_output(parsed, schema)
    if not errors:
        return ""
    joined = "; ".join(str(e) for e in errors)
    return f"the artifact does not conform to `validate.schema`: {joined}"


def build_advisory_approver(
    *,
    governance: Any,
    topology_id: str,
    agent_id: str,
    gate_id: str,
    declares_approve: bool,
) -> Approver:
    """The ``approve`` layer on a path that has no durable parking.

    ``approve`` is the sole predecessor of END — the structural invariant that stops an advisory
    layer from ever deciding — so a gate that runs at all must have an approver. On the in-node
    path there is nothing a human can be parked in: `resolve_multiparty` would poll the review
    queue inside the agent's coroutine for up to seven days, holding the run and the model session,
    and losing the wait entirely on a serve restart. A `swarmkit run` from a terminal could not
    approve at all.

    So here the layer records and passes. Human approval on the pipeline path is the stage-level
    ``gate:``, which parks the saga durably and survives restarts — the mechanism that already
    exists for exactly this, rather than a second one that blocks a coroutine for a week.

    It is not a silent pass. Every advisory failure that reached this layer is audited, and a
    funnel that declares ``approve:`` is warned about at compile time (`_compiler.py`), because a
    quality gate downgraded to nothing without saying so is the defect this whole chain is made of.
    """
    from swarmkit_runtime.review._multiparty import _audit  # noqa: PLC0415

    async def approver(state: FunnelGateState) -> ApproveOutcome:
        provenance = dict(state.get("provenance") or {})
        judge = provenance.get("judge") or {}
        review = provenance.get("review") or {}
        failed = [
            name
            for name, ok in (
                ("validate", provenance.get("validate_ok", True)),
                ("judge", judge.get("passed", True)),
                ("review", not review.get("route_back")),
            )
            if not ok
        ]
        await _audit(
            governance,
            "funnel.advisory_completed",
            agent_id,
            {
                "gate_id": gate_id,
                "topology_id": topology_id,
                "failed_layers": failed,
                # WHAT was wrong, not just which layer. The field-level errors exist at this
                # moment and used to be discarded, so a reader who queried the audit log learned
                # only that validation failed and had to re-run the validator by hand to find out
                # why. `failed_layers: ["validate"]` alone is not actionable.
                "critique": str(provenance.get("critique") or "")[:4000],
                "judge_score": (judge or {}).get("score"),
                "retries": provenance.get("retries", 0),
                "escalated": provenance.get("escalated", False),
                # Stated on every record, so a reader of the audit log never has to infer why a
                # declared approve layer produced no approval event.
                "approve": "deferred to the stage gate" if declares_approve else "not declared",
            },
        )
        if failed:
            logger.warning(
                "funnel gate on %r/%r: %s failed after %d retries — the artifact proceeds with the "
                "failure recorded. Human approval is the stage-level `gate:`, not this layer.",
                topology_id,
                agent_id,
                " and ".join(failed),
                provenance.get("retries", 0),
            )
        return ApproveOutcome(approved=True, detail="advisory layers only; see the stage gate")

    return approver


def _read_rubric(raw: Any, declared_in: Path | None, workspace_root: Path | None) -> str:
    """Load ``judge.rubric``, or return "" when there is none / it cannot be read.

    The schema calls it workspace-relative, so the workspace root is tried first; the declaring
    funnel's directory is a fallback, matching how `validate.schema` resolves. An unreadable rubric
    warns and the judge still runs unscored-against-it rather than the layer vanishing — a judge
    that stops working because a path is wrong is worse than one judging without the document, and
    silence is worse than both.
    """
    if not raw or not isinstance(raw, str):
        return ""
    bases = [b for b in (workspace_root, declared_in.parent if declared_in else None) if b]
    for base in bases:
        candidate = Path(base) / raw
        try:
            if candidate.is_file():
                return str(candidate.read_text())
        except OSError:  # pragma: no cover - unreadable file, same outcome as missing
            continue
    logger.warning(
        "funnel `judge.rubric` %r could not be read (looked in %s) — the judge will run without "
        "it. The rubric is what the judge scores against, so a score produced without it is not "
        "the score the funnel asked for.",
        raw,
        " and ".join(str(b) for b in bases) or "nowhere: no workspace root given",
    )
    return ""


def build_decision_judge(
    spec: dict[str, Any],
    *,
    governance: Any,
    agent_id: str,
    declared_in: Path | None = None,
    workspace_root: Path | None = None,
) -> Judge | None:
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
    # `judge.rubric` was declared in the schema, accepted by validation, displayed — and read by
    # nothing, so every workspace using it had to repeat the rubric inside the skill prompt. Loaded
    # once at build time: it is a file on disk, not something to re-read per artifact.
    rubric = _read_rubric(judge_cfg.get("rubric"), declared_in, workspace_root)

    async def judge(artifact: str) -> JudgeOutcome:
        result = await governance.evaluate_decision_skill(
            skill_id=skill_id,
            trigger="post_output",
            agent_id=agent_id,
            content=artifact,
            context={"rubric": rubric} if rubric else None,
        )
        passed = result.verdict == "pass" and result.confidence >= threshold
        return JudgeOutcome(passed=passed, score=result.confidence, critique=result.reasoning)

    return judge


def build_deterministic_validator(
    spec: dict[str, Any],
    *,
    declared_in: Path | None = None,
    workspace_root: Path | None = None,
) -> Validator | None:
    """Bind the funnel's ``validate`` layer to deterministic checks (design/details/
    funnel-deterministic-validate.md).

    Composes the configured sibling checks — both run, both must pass (an artifact is only as valid
    as its weakest deterministic guard); the first failure's verdict becomes the retry critique,
    which drives the funnel's bounded retry and escalates to the human ``approve`` on exhaustion:

    * ``validate.slice_budget`` — enforces slice size on the produced diff (``ctx.diff``, falling
      back to the artifact when no separate diff was threaded, e.g. a diff-only drafter or a test).
    * ``validate.cited_change`` — reads ``ctx.artifact`` as a change-rationale and resolves its
      citations against ``ctx.diff``; an uncited change fails.

    * ``validate.schema`` — the artifact must conform to the named JSON Schema.

    This docstring used to say a schema-only validate "stays handled by output governance", and it
    was not: ``output_schema`` is merged from the AGENT and its ARCHETYPE only
    (``_merge_output_schema`` in the resolver), never from the funnel, and nothing bridged the two.
    So a funnel declaring ``validate: {schema: ...}`` wired no validate node and no other check
    either — three consecutive specs shipped with ``code_changes`` entries whose ``kind`` and
    ``action`` are not in their own schema's enums, read and approved by a human against a contract
    nothing had enforced.

    Returns ``None`` when no deterministic check is configured.
    """
    validate_cfg = spec.get("validate") or {}
    budget = validate_cfg.get("slice_budget")
    cited = bool(validate_cfg.get("cited_change"))
    schema = _resolve_validate_schema(validate_cfg, declared_in, workspace_root)
    if not budget and not cited and schema is None:
        return None

    from swarmkit_runtime.cited_change import check_rationale  # noqa: PLC0415
    from swarmkit_runtime.slice_budget import check_diff_text  # noqa: PLC0415

    max_diff_lines = budget.get("max_diff_lines") if budget else None
    max_files = budget.get("max_files") if budget else None

    async def validator(ctx: ValidateContext) -> ValidateOutcome:
        diff = ctx.diff if ctx.diff is not None else ctx.artifact
        if schema is not None:
            detail = _schema_verdict(ctx.artifact, schema)
            if detail:
                return ValidateOutcome(ok=False, artifact=ctx.artifact, detail=detail)
        if budget:
            result = check_diff_text(diff, max_diff_lines=max_diff_lines, max_files=max_files)
            if not result.within_budget:
                return ValidateOutcome(ok=False, artifact=ctx.artifact, detail=result.verdict())
        if cited:
            cov = check_rationale(ctx.artifact, ctx.diff or "")
            if not cov.ok:
                return ValidateOutcome(ok=False, artifact=ctx.artifact, detail=cov.verdict())
        return ValidateOutcome(ok=True, artifact=ctx.artifact)

    return validator


async def run_agent_funnel_gate(
    funnel_spec: dict[str, Any],
    *,
    produce: Callable[[str | None], Awaitable[str]],
    governance: Any,
    # Only the multi-party approver reads these. A caller supplying its own ``approver`` needs
    # neither — which is the whole reason the compiler's guard on `review_queue` was wrong: it
    # gated validate/judge/review, none of which touch a queue, behind the one layer that does.
    review_queue: Any = None,
    role_registry: Any = None,
    topology_id: str,
    agent_id: str,
    gate_id: str | None = None,
    author: str | None = None,
    initial_artifact: str = "",
    diff_source: DiffSource | None = None,
    approver: Approver | None = None,
    #: where the funnel was declared, so `validate.schema` resolves against it (as `output_schema`
    #: resolves against the artifact that declared it) rather than against the process cwd.
    funnel_source_path: Path | None = None,
    workspace_root: Path | None = None,
    #: pre-built layers. The compiler builds these once at wrap time — both so they are not
    #: reconstructed on every invocation, and so the wiring ledger can record what was actually
    #: built at the moment it is built (`swarmkit_runtime.reachability`).
    judge: Judge | None = None,
    validator: Validator | None = None,
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

    gate_judge = judge or build_decision_judge(
        funnel_spec, governance=governance, agent_id=agent_id
    )
    gate_approver = approver or build_multiparty_approver(
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
    gate_validator = validator or build_deterministic_validator(
        funnel_spec, declared_in=funnel_source_path, workspace_root=workspace_root
    )
    compiled = compile_funnel_gate(
        funnel_spec,
        drafter=drafter,
        approver=gate_approver,
        judge=gate_judge,
        validator=gate_validator,
        diff_source=diff_source,
    )
    result = await compiled.ainvoke({"artifact": initial_artifact, "retries": 0})
    return cast(FunnelGateState, result)
