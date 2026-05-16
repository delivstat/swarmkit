"""Decision skill gate — fires mandatory decision skills at trigger points.

Governance decision skills are evaluated by the GovernanceProvider at
three points: post_output, checkpoint, and pre_synthesis. This module
provides the hook functions called by the compiler and task executor.

See ``design/details/governance-decision-skills.md``.
"""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.governance import (
    DecisionSkillBinding,
    DecisionSkillResult,
    GovernanceProvider,
)

from ._helpers import _progress

_MAX_RETRIES = 2


async def evaluate_post_output(
    *,
    agent_id: str,
    output: str,
    bindings: list[DecisionSkillBinding],
    governance: GovernanceProvider,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[DecisionSkillResult]]:
    """Evaluate post_output decision skills against agent output.

    Returns (possibly revised output, list of all results).
    If any binding returns fail, the output is annotated with
    flagged items for the coordinator to see.
    """
    applicable = [b for b in bindings if b.trigger == "post_output" and b.applies_to(agent_id)]
    if not applicable:
        return output, []

    results: list[DecisionSkillResult] = []
    for binding in applicable:
        result = await governance.evaluate_decision_skill(
            skill_id=binding.id,
            trigger="post_output",
            agent_id=agent_id,
            content=output,
            context=context,
        )
        results.append(result)
        if result.verdict == "fail":
            _progress(
                f"  [{agent_id}] decision skill '{binding.id}' failed: {result.reasoning[:80]}"
            )

    failed = [r for r in results if r.verdict == "fail"]
    if failed:
        flags = []
        for r in failed:
            flags.append(f"[{r.skill_id}]: {r.reasoning}")
            for item in r.flagged_items:
                flags.append(f"  - {item}")
        annotation = "\n\n---\nGOVERNANCE FLAGS:\n" + "\n".join(flags)
        return output + annotation, results

    return output, results


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
            context={"task_ids": list(task_results.keys())},
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
) -> list[DecisionSkillResult]:
    """Evaluate pre_synthesis decision skills before coordinator synthesizes.

    Returns list of results. Failed results are injected into the
    synthesis prompt as known issues.
    """
    applicable = [b for b in bindings if b.trigger == "pre_synthesis" and b.applies_to(agent_id)]
    if not applicable:
        return []

    combined = f"Original request: {original_input}\n\n"
    combined += "\n\n".join(f"[{tid}]:\n{text}" for tid, text in task_results.items())

    results: list[DecisionSkillResult] = []
    for binding in applicable:
        result = await governance.evaluate_decision_skill(
            skill_id=binding.id,
            trigger="pre_synthesis",
            agent_id=agent_id,
            content=combined,
            context={
                "task_ids": list(task_results.keys()),
                "original_input": original_input,
            },
        )
        results.append(result)
        if result.verdict == "fail":
            _progress(
                f"  [{agent_id}] pre_synthesis skill '{binding.id}' failed: {result.reasoning[:80]}"
            )

    return results


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
