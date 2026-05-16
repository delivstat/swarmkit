"""Decision skill evaluator — invokes decision skills via the skill executor.

When a GovernanceProvider receives an evaluate_decision_skill call, it
delegates to this evaluator which looks up the skill, executes it via
llm_prompt, and parses the structured JSON response into a
DecisionSkillResult.

See ``design/details/governance-decision-skills.md``.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.skills import ResolvedSkill


async def evaluate_skill(
    *,
    skill: ResolvedSkill,
    agent_id: str,
    trigger: str,
    content: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    context: dict[str, Any] | None = None,
) -> DecisionSkillResult:
    """Invoke a decision skill and parse the result."""
    from swarmkit_runtime.langgraph_compiler._skill_executor import (  # noqa: PLC0415
        execute_skill as run_skill,
    )

    input_text = _build_input(content, trigger, agent_id, context)

    raw_result = await run_skill(
        skill,
        input_text=input_text,
        model_provider=model_provider,
        model_name=model_name,
    )

    result_text = raw_result if isinstance(raw_result, str) else raw_result[0]
    return _parse_result(skill.id, result_text)


def _build_input(
    content: str,
    trigger: str,
    agent_id: str,
    context: dict[str, Any] | None,
) -> str:
    """Build the input text for the decision skill."""
    parts = [
        f"Trigger: {trigger}",
        f"Agent: {agent_id}",
    ]

    if context and "scope" in context:
        scope_data = context["scope"]
        parts.append("")
        parts.append("--- FROZEN SCOPE (from Jira ticket) ---")
        if isinstance(scope_data, dict):
            parts.append(json.dumps(scope_data, indent=2, default=str))
        else:
            parts.append(str(scope_data))
        parts.append("--- END SCOPE ---")

    parts.append("")
    parts.append("--- CONTENT TO EVALUATE ---")
    parts.append(content)
    parts.append("--- END CONTENT ---")

    remaining_context = {k: v for k, v in (context or {}).items() if k != "scope"}
    if remaining_context:
        parts.append(f"\nContext: {json.dumps(remaining_context, default=str)}")
    return "\n".join(parts)


def _parse_result(skill_id: str, raw: str) -> DecisionSkillResult:
    """Parse JSON result from a decision skill into DecisionSkillResult."""
    try:
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        data = json.loads(cleaned)
        verdict = data.get("verdict", "pass")
        if verdict not in ("pass", "fail", "needs-revision"):
            verdict = "pass"

        flagged: list[str] = []
        for key in ("flagged_items", "uncited_claims", "contradictions"):
            items = data.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str):
                        flagged.append(item)
                    elif isinstance(item, dict):
                        desc = item.get("claim") or item.get("description") or str(item)
                        flagged.append(desc)

        return DecisionSkillResult(
            skill_id=skill_id,
            verdict=verdict,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=str(data.get("reasoning", "")),
            flagged_items=flagged,
        )
    except (json.JSONDecodeError, ValueError, KeyError):
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict="pass",
            confidence=0.0,
            reasoning=f"Failed to parse decision skill output: {raw[:200]}",
        )
