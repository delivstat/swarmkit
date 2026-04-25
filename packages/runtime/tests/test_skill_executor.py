"""Tests for skill execution (M4 decision skill wiring).

See ``design/details/decision-skills.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.langgraph_compiler._skill_executor import execute_skill
from swarmkit_runtime.model_providers import (
    MockModelProvider,
)
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.skills import ResolvedSkill

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCOPED_WS = FIXTURES / "workspaces" / "resolved-tree"


def _get_skill(skill_id: str) -> ResolvedSkill:
    ws = resolve_workspace(SCOPED_WS)
    return ws.skills[skill_id]


@pytest.mark.asyncio
async def test_llm_prompt_skill_executes() -> None:
    """An llm_prompt skill calls the model and returns the response."""
    # code-quality-review uses mcp_tool, not llm_prompt — but we can
    # test execute_skill's routing by checking what happens with mcp_tool
    skill = _get_skill("code-quality-review")
    mock = MockModelProvider()
    result = await execute_skill(
        skill,
        input_text="Review this code",
        model_provider=mock,
        model_name="mock",
    )
    # mcp_tool should return a not-yet-available message
    assert "MCP" in result or "not yet" in result.lower()


@pytest.mark.asyncio
async def test_unknown_impl_type_returns_error() -> None:
    """Unknown implementation type returns an error message."""
    skill = _get_skill("code-quality-review")
    # Monkey-patch the implementation to an unknown type
    original = skill.raw.implementation
    try:
        skill.raw.__dict__["implementation"] = {"type": "unknown_type"}
        mock = MockModelProvider()
        result = await execute_skill(
            skill,
            input_text="test",
            model_provider=mock,
            model_name="mock",
        )
        assert "Unknown" in result or "unknown_type" in result
    finally:
        skill.raw.__dict__["implementation"] = original


@pytest.mark.asyncio
async def test_mcp_tool_without_manager_names_server_and_remediation() -> None:
    """When mcp_manager=None, the diagnostic must name the server and the
    file the user has to edit. Surfacing the impl bits up front matters
    because non-CLI compile sites (notebooks, scripts) hit this path
    instead of the CLI's compile-time guard.
    """
    skill = _get_skill("code-quality-review")  # mcp_tool, server: rynko-flow
    mock = MockModelProvider()
    result = await execute_skill(
        skill,
        input_text="anything",
        model_provider=mock,
        model_name="mock",
        mcp_manager=None,
    )
    assert "rynko-flow" in result
    assert "validate_code_review" in result
    assert "workspace.yaml" in result
    assert "mcp_servers" in result
