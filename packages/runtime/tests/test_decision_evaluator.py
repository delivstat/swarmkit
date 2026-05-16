"""Tests for decision skill evaluator and skill-backed governance."""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance._decision_evaluator import _parse_result
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.governance._skill_backed import SkillBackedGovernanceProvider
from swarmkit_runtime.model_providers import MockModelProvider


class TestParseResult:
    def test_valid_json_pass(self) -> None:
        raw = '{"verdict": "pass", "confidence": 0.9, "reasoning": "All good"}'
        result = _parse_result("test-skill", raw)
        assert result.verdict == "pass"
        assert result.confidence == 0.9
        assert result.reasoning == "All good"
        assert result.flagged_items == []

    def test_valid_json_fail_with_flagged(self) -> None:
        raw = (
            '{"verdict": "fail", "confidence": 0.8, '
            '"reasoning": "Issues found", '
            '"flagged_items": ["unsourced claim about X", "fabricated name Y"]}'
        )
        result = _parse_result("test-skill", raw)
        assert result.verdict == "fail"
        assert len(result.flagged_items) == 2
        assert "unsourced claim about X" in result.flagged_items

    def test_json_in_code_block(self) -> None:
        raw = '```json\n{"verdict": "pass", "confidence": 1.0, "reasoning": "ok"}\n```'
        result = _parse_result("test-skill", raw)
        assert result.verdict == "pass"

    def test_uncited_claims_key(self) -> None:
        raw = (
            '{"verdict": "fail", "confidence": 0.7, '
            '"reasoning": "Missing citations", '
            '"uncited_claims": [{"claim": "API X exists", "severity": "major"}]}'
        )
        result = _parse_result("test-skill", raw)
        assert result.verdict == "fail"
        assert "API X exists" in result.flagged_items

    def test_contradictions_key(self) -> None:
        raw = (
            '{"verdict": "fail", "confidence": 0.6, '
            '"reasoning": "Contradiction", '
            '"contradictions": [{"claim_a": "X is up", "source_a": "agent-1", '
            '"claim_b": "X is down", "source_b": "agent-2", "severity": "major"}]}'
        )
        result = _parse_result("test-skill", raw)
        assert result.verdict == "fail"
        assert len(result.flagged_items) == 1

    def test_invalid_json_returns_pass(self) -> None:
        raw = "This is not JSON at all"
        result = _parse_result("test-skill", raw)
        assert result.verdict == "pass"
        assert result.confidence == 0.0
        assert "Failed to parse" in result.reasoning

    def test_invalid_verdict_defaults_to_pass(self) -> None:
        raw = '{"verdict": "maybe", "confidence": 0.5, "reasoning": "unsure"}'
        result = _parse_result("test-skill", raw)
        assert result.verdict == "pass"


class TestSkillBackedGovernanceProvider:
    @pytest.mark.asyncio
    async def test_delegates_standard_methods(self) -> None:
        base = MockGovernanceProvider(allow_all=True)
        provider = SkillBackedGovernanceProvider(
            base=base,
            skills={},
            model_provider=MockModelProvider(),
            model_name="mock",
        )
        decision = await provider.evaluate_action(
            agent_id="test",
            action="read",
            scopes_required=frozenset({"read"}),
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_missing_skill_returns_pass(self) -> None:
        base = MockGovernanceProvider(allow_all=True)
        provider = SkillBackedGovernanceProvider(
            base=base,
            skills={},
            model_provider=MockModelProvider(),
            model_name="mock",
        )
        result = await provider.evaluate_decision_skill(
            skill_id="nonexistent-skill",
            trigger="post_output",
            agent_id="test-agent",
            content="some output",
        )
        assert result.verdict == "pass"
        assert "not found" in result.reasoning
