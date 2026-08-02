"""Decision skill gate — fires mandatory decision skills at trigger points.

Governance decision skills are evaluated by the GovernanceProvider at
four points: pre_input, post_output, checkpoint, and pre_synthesis.
This module provides the hook functions called by the compiler and task
executor.

See ``design/details/governance-decision-skills.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from swarmkit_runtime.governance import (
    DecisionSkillBinding,
    DecisionSkillResult,
    GovernanceProvider,
)

from ._helpers import _progress

_DEFAULT_MAX_RETRIES = 4

RetryFn = Callable[[str], Awaitable[str]]


def _binding_context(
    binding: DecisionSkillBinding, context: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Merge a binding's `config:` block into the evaluation context.

    The schema calls `config` "skill-specific configuration" and nothing read it, so a binding
    could carry a whole tuning block that did nothing — declarable, documented, inert. It reaches
    the skill under a `config` key; the trigger's own context keys win on collision, because those
    describe the thing being evaluated and a binding must not be able to overwrite them.
    """
    if not binding.config:
        return context
    return {"config": dict(binding.config), **(context or {})}


def _effective_max_retries(bindings: list[DecisionSkillBinding], default: int) -> int:
    """`max_retries` from the bindings' config, else *default*.

    The retry loop is shared by every applicable binding, so per-binding values have to collapse
    to one number: take the largest declared. A binding asking for more attempts should not be
    capped by a stricter sibling — the loop exits as soon as ALL of them pass anyway.
    """
    declared = []
    for binding in bindings:
        raw = binding.config.get("max_retries")
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            declared.append(value)
    return max(declared) if declared else default


async def evaluate_pre_input(
    *,
    agent_id: str,
    user_input: str,
    bindings: list[DecisionSkillBinding],
    governance: GovernanceProvider,
) -> tuple[bool, str | None, list[DecisionSkillResult]]:
    """Evaluate pre_input decision skills against user input before any LLM work.

    Returns (should_proceed, rejection_message, results). If any binding
    fails, the agent should return the rejection message instead of
    proceeding with the LLM call — saving tokens on off-topic or
    malicious queries.
    """
    applicable = [b for b in bindings if b.trigger == "pre_input" and b.applies_to(agent_id)]
    if not applicable:
        return True, None, []

    results: list[DecisionSkillResult] = []
    for binding in applicable:
        result = await governance.evaluate_decision_skill(
            skill_id=binding.id,
            trigger="pre_input",
            agent_id=agent_id,
            content=user_input,
            context=_binding_context(binding, None),
        )
        results.append(result)

    failed = [r for r in results if r.verdict == "fail"]
    if not failed:
        return True, None, results

    # Extract suggested_response from raw data if the skill provided one,
    # otherwise fall back to the skill's reasoning.
    for r in failed:
        raw = getattr(r, "raw", None) or {}
        suggested = raw.get("suggested_response") if isinstance(raw, dict) else None
        if suggested:
            _progress(f"  [{agent_id}] pre_input skill '{r.skill_id}' rejected: {r.reasoning[:80]}")
            return False, str(suggested), results

    # No suggested_response found — use first failure's reasoning
    first_fail = failed[0]
    _progress(
        f"  [{agent_id}] pre_input skill '{first_fail.skill_id}' rejected: "
        f"{first_fail.reasoning[:80]}"
    )
    return False, first_fail.reasoning, results


async def evaluate_post_output(
    *,
    agent_id: str,
    output: str,
    bindings: list[DecisionSkillBinding],
    governance: GovernanceProvider,
    context: dict[str, Any] | None = None,
    retry_fn: RetryFn | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> tuple[str, list[DecisionSkillResult]]:
    """Evaluate post_output decision skills against agent output.

    If a retry_fn is provided and evaluation fails, the agent is
    asked to revise its output using the governance feedback. The
    agent already has its research context — it just needs to fix
    citations, remove fabricated names, etc. Retries up to
    max_retries times before annotating and passing through.

    Returns (final output, list of all results from last evaluation).
    """
    applicable = [b for b in bindings if b.trigger == "post_output" and b.applies_to(agent_id)]
    if not applicable:
        return output, []

    current_output = output
    last_results: list[DecisionSkillResult] = []
    # An explicit `max_retries` in the binding overrides the caller's default; neither call site
    # passed one, so the value in workspace.yaml was ignored entirely.
    max_retries = _effective_max_retries(applicable, max_retries)

    for attempt in range(max_retries + 1):
        last_results = []
        for binding in applicable:
            result = await governance.evaluate_decision_skill(
                skill_id=binding.id,
                trigger="post_output",
                agent_id=agent_id,
                content=current_output,
                context=_binding_context(binding, context),
            )
            last_results.append(result)

        failed = [r for r in last_results if r.verdict == "fail"]
        if not failed:
            return current_output, last_results

        if attempt < max_retries and retry_fn is not None:
            feedback = _build_retry_feedback(failed)
            _progress(
                f"  [{agent_id}] governance retry {attempt + 1}/{max_retries}: "
                f"{len(failed)} issue(s)"
            )
            current_output = await retry_fn(feedback)
        else:
            if attempt == max_retries:
                _progress(
                    f"  [{agent_id}] governance retries exhausted "
                    f"({max_retries}), passing with flags"
                )
            break

    failed = [r for r in last_results if r.verdict == "fail"]
    if failed:
        flags = []
        for r in failed:
            flags.append(f"[{r.skill_id}]: {r.reasoning}")
            for item in r.flagged_items:
                flags.append(f"  - {item}")
        annotation = "\n\n---\nGOVERNANCE FLAGS:\n" + "\n".join(flags)
        return current_output + annotation, last_results

    return current_output, last_results


def _build_retry_feedback(failed: list[DecisionSkillResult]) -> str:
    """Build feedback from decision skill results for the agent to revise.

    Passes through the skill's own reasoning and flagged items — the
    gate doesn't inject domain-specific instructions since only the
    skill knows what kind of issue it found.
    """
    lines = ["Revise your output to address the following issues:\n"]
    for r in failed:
        lines.append(f"[{r.skill_id}]: {r.reasoning}")
        for item in r.flagged_items:
            lines.append(f"  - {item}")
    lines.append("\nRewrite your COMPLETE response with these issues fixed.")
    return "\n".join(lines)


async def evaluate_checkpoint(
    *,
    agent_id: str,
    task_results: dict[str, str],
    bindings: list[DecisionSkillBinding],
    governance: GovernanceProvider,
) -> list[DecisionSkillResult]:
    """Evaluate checkpoint decision skills against all completed task results.

    Returns list of results. Failed results are injected into the
    coordinator's checkpoint review as feedback.
    """
    applicable = [b for b in bindings if b.trigger == "checkpoint" and b.applies_to(agent_id)]
    if not applicable:
        return []

    combined = "\n\n".join(f"[{tid}]:\n{text}" for tid, text in task_results.items())

    results: list[DecisionSkillResult] = []
    for binding in applicable:
        result = await governance.evaluate_decision_skill(
            skill_id=binding.id,
            trigger="checkpoint",
            agent_id=agent_id,
            content=combined,
            context=_binding_context(binding, {"task_ids": list(task_results.keys())}),
        )
        results.append(result)
        if result.verdict == "fail":
            _progress(
                f"  [{agent_id}] checkpoint skill '{binding.id}' failed: {result.reasoning[:80]}"
            )

    return results


async def evaluate_pre_synthesis(
    *,
    agent_id: str,
    task_results: dict[str, str],
    original_input: str,
    bindings: list[DecisionSkillBinding],
    governance: GovernanceProvider,
    workspace_root: Any = None,
) -> list[DecisionSkillResult]:
    """Evaluate pre_synthesis decision skills before coordinator synthesizes.

    Reads scope.json from run state (if present) and passes it as
    context to the decision skill. This enables spec-conformance
    checking against the frozen scope.
    """
    applicable = [b for b in bindings if b.trigger == "pre_synthesis" and b.applies_to(agent_id)]
    if not applicable:
        return []

    combined = f"Original request: {original_input}\n\n"
    combined += "\n\n".join(f"[{tid}]:\n{text}" for tid, text in task_results.items())

    scope = _read_scope(workspace_root)
    context: dict[str, Any] = {
        "task_ids": list(task_results.keys()),
        "original_input": original_input,
    }
    if scope:
        context["scope"] = scope

    results: list[DecisionSkillResult] = []
    for binding in applicable:
        result = await governance.evaluate_decision_skill(
            skill_id=binding.id,
            trigger="pre_synthesis",
            agent_id=agent_id,
            content=combined,
            context=_binding_context(binding, context),
        )
        results.append(result)
        if result.verdict == "fail":
            _progress(
                f"  [{agent_id}] pre_synthesis skill '{binding.id}' failed: {result.reasoning[:80]}"
            )

    return results


def _read_scope(workspace_root: Any) -> dict[str, Any] | None:
    """Read the current run's scope via the canonical ScopeStore.

    Previously this hardcoded ``run-state/current/scope.json`` — the stale ``current/`` layout that
    broke once run-state became run-id-scoped, so the gate never saw the frozen scope. It now reads
    the same run-scoped path every writer uses. *workspace_root* is unused but kept for callers.
    """
    from ._scope import read_scope  # noqa: PLC0415

    try:
        return read_scope()
    except Exception:  # no active run context (e.g. isolated unit use) — behave as "no scope"
        return None


def format_gate_feedback(results: list[DecisionSkillResult]) -> str:
    """Format failed decision skill results as feedback for the coordinator."""
    failed = [r for r in results if r.verdict in ("fail", "needs-revision")]
    if not failed:
        return ""
    lines = ["GOVERNANCE FEEDBACK (address before proceeding):"]
    for r in failed:
        lines.append(f"- [{r.skill_id}] {r.reasoning}")
        for item in r.flagged_items:
            lines.append(f"    - {item}")
    return "\n".join(lines)
