"""Tests for governance decision skill bindings and merge logic."""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance import (
    DecisionSkillBinding,
    DecisionSkillResult,
    merge_decision_skills,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider


class TestDecisionSkillBinding:
    def test_applies_to_all_agents_by_default(self) -> None:
        binding = DecisionSkillBinding(id="grounding-verifier", trigger="post_output")
        assert binding.applies_to("any-agent")
        assert binding.applies_to("another-agent")

    def test_applies_to_scoped_agents(self) -> None:
        binding = DecisionSkillBinding(
            id="citation-checker",
            trigger="post_output",
            scope="jira-researcher,docs-researcher",
        )
        assert binding.applies_to("jira-researcher")
        assert binding.applies_to("docs-researcher")
        assert not binding.applies_to("config-analyst")

    def test_applies_to_wildcard(self) -> None:
        binding = DecisionSkillBinding(id="verifier", trigger="checkpoint", scope="*")
        assert binding.applies_to("anything")

    def test_default_required_is_true(self) -> None:
        binding = DecisionSkillBinding(id="verifier", trigger="post_output")
        assert binding.required is True


class TestMergeDecisionSkills:
    def test_workspace_only(self) -> None:
        workspace = [
            {"id": "grounding-verifier", "trigger": "post_output"},
            {"id": "contradiction-detector", "trigger": "pre_synthesis"},
        ]
        result = merge_decision_skills(workspace, [])
        assert len(result) == 2
        ids = {b.id for b in result}
        assert ids == {"grounding-verifier", "contradiction-detector"}

    def test_topology_extends(self) -> None:
        workspace = [{"id": "grounding-verifier", "trigger": "post_output"}]
        topology = [{"id": "citation-checker", "trigger": "post_output"}]
        result = merge_decision_skills(workspace, topology)
        assert len(result) == 2
        ids = {b.id for b in result}
        assert ids == {"grounding-verifier", "citation-checker"}

    def test_topology_overrides(self) -> None:
        workspace = [
            {"id": "grounding-verifier", "trigger": "post_output", "scope": "*"},
        ]
        topology = [
            {
                "id": "grounding-verifier",
                "trigger": "post_output",
                "scope": "jira-researcher",
            },
        ]
        result = merge_decision_skills(workspace, topology)
        assert len(result) == 1
        assert result[0].scope == "jira-researcher"

    def test_topology_disables_workspace_binding(self) -> None:
        workspace = [
            {"id": "grounding-verifier", "trigger": "post_output"},
            {"id": "contradiction-detector", "trigger": "pre_synthesis"},
        ]
        topology = [
            {"id": "grounding-verifier", "trigger": "post_output", "required": False},
        ]
        result = merge_decision_skills(workspace, topology)
        assert len(result) == 1
        assert result[0].id == "contradiction-detector"

    def test_empty_both(self) -> None:
        result = merge_decision_skills([], [])
        assert result == []

    def test_config_passed_through(self) -> None:
        workspace = [
            {
                "id": "grounding-verifier",
                "trigger": "post_output",
                "config": {"max_retries": 3},
            },
        ]
        result = merge_decision_skills(workspace, [])
        assert result[0].config == {"max_retries": 3}


class TestMockGovernanceDecisionSkills:
    @pytest.mark.asyncio
    async def test_default_passes(self) -> None:
        gov = MockGovernanceProvider(allow_all=True)
        result = await gov.evaluate_decision_skill(
            skill_id="grounding-verifier",
            trigger="post_output",
            agent_id="test-agent",
            content="some output",
        )
        assert result.verdict == "pass"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_configured_verdict(self) -> None:
        fail_result = DecisionSkillResult(
            skill_id="grounding-verifier",
            verdict="fail",
            confidence=0.9,
            reasoning="Found unsourced claims",
            flagged_items=["Claim about API X is unsourced"],
        )
        gov = MockGovernanceProvider(
            allow_all=True,
            decision_skill_verdicts={"grounding-verifier": fail_result},
        )
        result = await gov.evaluate_decision_skill(
            skill_id="grounding-verifier",
            trigger="post_output",
            agent_id="test-agent",
            content="some output with fabricated data",
        )
        assert result.verdict == "fail"
        assert len(result.flagged_items) == 1

    @pytest.mark.asyncio
    async def test_unconfigured_skill_passes(self) -> None:
        fail_result = DecisionSkillResult(
            skill_id="grounding-verifier",
            verdict="fail",
            confidence=0.9,
            reasoning="Found issues",
        )
        gov = MockGovernanceProvider(
            allow_all=True,
            decision_skill_verdicts={"grounding-verifier": fail_result},
        )
        result = await gov.evaluate_decision_skill(
            skill_id="citation-checker",
            trigger="post_output",
            agent_id="test-agent",
            content="some output",
        )
        assert result.verdict == "pass"
