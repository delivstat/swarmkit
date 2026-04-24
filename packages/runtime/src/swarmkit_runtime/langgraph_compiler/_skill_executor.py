"""Skill execution for the compiler's agentic tool-use loop.

Currently supports ``llm_prompt`` skills (decision skills, judges).
``mcp_tool`` skills land in M5. ``composed`` skills land with panel
aggregation.

See ``design/details/decision-skills.md``.
"""

from __future__ import annotations

from swarmkit_runtime.model_providers import (
    CompletionRequest,
    Message,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.skills import ResolvedSkill


async def execute_skill(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Execute a skill and return the result as a string.

    Dispatches by implementation type.
    """
    impl = skill.raw.implementation
    impl_type = impl.get("type") if isinstance(impl, dict) else getattr(impl, "type", None)

    if impl_type == "llm_prompt":
        return await _execute_llm_prompt(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=model_name,
        )

    if impl_type == "composed":
        return await _execute_composed(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=model_name,
        )

    if impl_type == "mcp_tool":
        return f"[skill:{skill.id}] MCP tool execution not yet available (M5)."

    return f"[skill:{skill.id}] Unknown implementation type: {impl_type}"


async def _execute_llm_prompt(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Execute an ``llm_prompt`` skill by calling the model with its prompt."""
    impl = skill.raw.implementation
    prompt = impl.get("prompt") if isinstance(impl, dict) else getattr(impl, "prompt", "")

    model_config = impl.get("model") if isinstance(impl, dict) else getattr(impl, "model", None)
    if model_config and isinstance(model_config, dict):
        model_name = model_config.get("name", model_name)

    system_prompt = str(prompt) if prompt else f"You are executing the skill: {skill.id}"

    response = await model_provider.complete(
        CompletionRequest(
            model=model_name,
            messages=(Message(role="user", content=input_text),),
            system=system_prompt,
        )
    )

    parts = [b.text for b in response.content if b.type == "text" and b.text]
    return "\n".join(parts) or "(no response)"


async def _execute_composed(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Execute a composed skill via panel aggregation."""
    from ._panel import execute_panel  # noqa: PLC0415

    constituent_skills = list(skill.resolved_composes)
    if not constituent_skills:
        return f"[skill:{skill.id}] No constituent skills resolved."

    return await execute_panel(
        skill,
        constituent_skills,
        input_text=input_text,
        model_provider=model_provider,
        model_name=model_name,
    )
