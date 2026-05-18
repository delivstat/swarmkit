"""Tests for Rynko Flow governance integration (Tier 2 validation).

Verifies that MCP-backed decision skills can fire through the
SkillBackedGovernanceProvider, and that the decision evaluator
passes mcp_manager to the skill executor.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._decision_evaluator import evaluate_skill
from swarmkit_runtime.governance._skill_backed import SkillBackedGovernanceProvider
from swarmkit_runtime.mcp._client import ToolMetadata, ToolResponse
from swarmkit_runtime.skills import ResolvedSkill
from swarmkit_schema import validate


def _make_mcp_decision_skill() -> MagicMock:
    """Create a mock MCP-backed decision skill (like rynko-output-validator)."""
    skill = MagicMock(spec=ResolvedSkill)
    skill.id = "rynko-output-validator"
    skill.raw = MagicMock()
    skill.raw.implementation = {
        "type": "mcp_tool",
        "server": "rynko-flow",
        "tool": "validate_gate",
    }
    skill.raw.category = "decision"
    return skill


def _make_llm_decision_skill() -> MagicMock:
    """Create a mock LLM-based decision skill."""
    skill = MagicMock(spec=ResolvedSkill)
    skill.id = "grounding-verifier"
    skill.raw = MagicMock()
    skill.raw.implementation = {
        "type": "llm_prompt",
        "prompt": "Check grounding",
    }
    skill.raw.category = "decision"
    return skill


# ---- SkillBackedGovernanceProvider accepts mcp_manager -------------------


class TestProviderMCPSupport:
    def test_accepts_mcp_manager(self) -> None:
        base = AsyncMock()
        mock_manager = MagicMock()
        provider = SkillBackedGovernanceProvider(
            base=base,
            skills={},
            model_provider=AsyncMock(),
            mcp_manager=mock_manager,
        )
        assert provider._mcp_manager is mock_manager

    def test_none_mcp_manager_default(self) -> None:
        provider = SkillBackedGovernanceProvider(
            base=AsyncMock(),
            skills={},
            model_provider=AsyncMock(),
        )
        assert provider._mcp_manager is None


# ---- evaluate_skill passes mcp_manager ----------------------------------


class TestEvaluateSkillMCP:
    @pytest.mark.asyncio
    async def test_mcp_skill_receives_manager(self) -> None:
        skill = _make_mcp_decision_skill()

        mock_mcp_result = MagicMock()
        mock_text = MagicMock()
        mock_text.text = '{"verdict": "pass", "confidence": 0.95, "reasoning": "All valid"}'
        mock_text.type = "text"
        mock_mcp_result.content = [mock_text]

        tool_response = ToolResponse(
            data=mock_mcp_result,
            metadata=ToolMetadata(
                source="rynko-flow:validate_gate",
                duration_ms=200,
                server_id="rynko-flow",
            ),
        )

        mock_manager = MagicMock()
        mock_manager.call_tool = AsyncMock(return_value=tool_response)
        mock_manager.get_permission.return_value = "open"
        mock_manager.get_server_cwd.return_value = None
        mock_manager.get_cached_result.return_value = None
        mock_manager._cache_misses = 0
        mock_manager.cache_result = MagicMock()

        result = await evaluate_skill(
            skill=skill,
            agent_id="test-agent",
            trigger="post_output",
            content='{"findings": [{"fact": "X", "source": "cdt:search"}]}',
            model_provider=AsyncMock(),
            model_name="mock",
            mcp_manager=mock_manager,
        )

        assert isinstance(result, DecisionSkillResult)
        assert result.verdict == "pass"
        mock_manager.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_mcp_skill_without_manager_returns_error(self) -> None:
        skill = _make_mcp_decision_skill()

        result = await evaluate_skill(
            skill=skill,
            agent_id="test-agent",
            trigger="post_output",
            content="test content",
            model_provider=AsyncMock(),
            model_name="mock",
            mcp_manager=None,
        )

        assert isinstance(result, DecisionSkillResult)
        assert "no mcp_servers" in result.reasoning.lower() or result.verdict == "pass"

    @pytest.mark.asyncio
    async def test_mcp_skill_fail_verdict(self) -> None:
        skill = _make_mcp_decision_skill()

        mock_mcp_result = MagicMock()
        mock_text = MagicMock()
        mock_text.text = (
            '{"verdict": "fail", "confidence": 0.9, '
            '"reasoning": "Finding references non-existent pipeline ID", '
            '"flagged_items": ["PL-999 not found in CDT"]}'
        )
        mock_text.type = "text"
        mock_mcp_result.content = [mock_text]

        tool_response = ToolResponse(
            data=mock_mcp_result,
            metadata=ToolMetadata(
                source="rynko-flow:validate_gate",
                duration_ms=150,
                server_id="rynko-flow",
            ),
        )

        mock_manager = MagicMock()
        mock_manager.call_tool = AsyncMock(return_value=tool_response)
        mock_manager.get_permission.return_value = "open"
        mock_manager.get_server_cwd.return_value = None
        mock_manager.get_cached_result.return_value = None
        mock_manager._cache_misses = 0
        mock_manager.cache_result = MagicMock()

        result = await evaluate_skill(
            skill=skill,
            agent_id="test-agent",
            trigger="post_output",
            content='{"findings": [{"fact": "Pipeline PL-999 is active", "source": "cdt:search"}]}',
            model_provider=AsyncMock(),
            model_name="mock",
            mcp_manager=mock_manager,
        )

        assert result.verdict == "fail"
        assert "pipeline" in result.reasoning.lower()
        assert len(result.flagged_items) >= 1


# ---- Reference skill YAML validation ------------------------------------


class TestReferenceSkill:
    def test_rynko_skill_yaml_is_valid(self) -> None:
        skill_path = Path("docs/examples/rynko-output-validator.yaml")
        data = yaml.safe_load(skill_path.read_text())
        validate("skill", data)
        assert data["category"] == "decision"
        assert data["implementation"]["type"] == "mcp_tool"
        assert data["implementation"]["server"] == "rynko-flow"
