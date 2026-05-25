"""Tests for pre_input decision gate trigger."""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._decision_gate import evaluate_pre_input


@pytest.fixture
def passing_gov() -> MockGovernanceProvider:
    return MockGovernanceProvider(allow_all=True)


@pytest.fixture
def failing_gov() -> MockGovernanceProvider:
    return MockGovernanceProvider(
        allow_all=True,
        decision_skill_verdicts={
            "relevance-gate": DecisionSkillResult(
                skill_id="relevance-gate",
                verdict="fail",
                confidence=0.95,
                reasoning="Query is off-topic for this workspace",
                raw={"suggested_response": "I can only help with Sterling OMS questions."},
            ),
        },
    )


@pytest.fixture
def bindings() -> list[DecisionSkillBinding]:
    return [
        DecisionSkillBinding(id="relevance-gate", trigger="pre_input"),
        DecisionSkillBinding(id="grounding-verifier", trigger="post_output"),
        DecisionSkillBinding(id="checkpoint-verifier", trigger="checkpoint"),
    ]


class TestPreInputFiltering:
    @pytest.mark.asyncio
    async def test_filters_only_pre_input_bindings(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        should_proceed, rejection, results = await evaluate_pre_input(
            agent_id="test-agent",
            user_input="How do I create an order?",
            bindings=bindings,
            governance=passing_gov,
        )
        assert should_proceed is True
        assert rejection is None
        # Only the pre_input binding should fire, not post_output or checkpoint
        assert len(results) == 1
        assert results[0].skill_id == "relevance-gate"

    @pytest.mark.asyncio
    async def test_no_applicable_bindings_proceeds(
        self,
        passing_gov: MockGovernanceProvider,
    ) -> None:
        non_pre_input_bindings = [
            DecisionSkillBinding(id="grounding-verifier", trigger="post_output"),
        ]
        should_proceed, rejection, results = await evaluate_pre_input(
            agent_id="test-agent",
            user_input="anything",
            bindings=non_pre_input_bindings,
            governance=passing_gov,
        )
        assert should_proceed is True
        assert rejection is None
        assert results == []


class TestPreInputPass:
    @pytest.mark.asyncio
    async def test_passing_verdict_allows_proceed(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        should_proceed, rejection, results = await evaluate_pre_input(
            agent_id="test-agent",
            user_input="How do I configure returns in Sterling?",
            bindings=bindings,
            governance=passing_gov,
        )
        assert should_proceed is True
        assert rejection is None
        assert len(results) == 1
        assert results[0].verdict == "pass"


class TestPreInputFail:
    @pytest.mark.asyncio
    async def test_failing_verdict_rejects_with_suggested_response(
        self,
        failing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        should_proceed, rejection, results = await evaluate_pre_input(
            agent_id="test-agent",
            user_input="What is the weather today?",
            bindings=bindings,
            governance=failing_gov,
        )
        assert should_proceed is False
        assert rejection == "I can only help with Sterling OMS questions."
        assert len(results) == 1
        assert results[0].verdict == "fail"

    @pytest.mark.asyncio
    async def test_failing_without_suggested_response_uses_reasoning(
        self,
    ) -> None:
        gov = MockGovernanceProvider(
            allow_all=True,
            decision_skill_verdicts={
                "relevance-gate": DecisionSkillResult(
                    skill_id="relevance-gate",
                    verdict="fail",
                    confidence=0.9,
                    reasoning="Off-topic query detected",
                ),
            },
        )
        bindings = [DecisionSkillBinding(id="relevance-gate", trigger="pre_input")]
        should_proceed, rejection, _ = await evaluate_pre_input(
            agent_id="test-agent",
            user_input="Tell me a joke",
            bindings=bindings,
            governance=gov,
        )
        assert should_proceed is False
        assert rejection == "Off-topic query detected"

    @pytest.mark.asyncio
    async def test_scope_filtering_skips_non_matching_agent(
        self,
        failing_gov: MockGovernanceProvider,
    ) -> None:
        scoped_bindings = [
            DecisionSkillBinding(
                id="relevance-gate",
                trigger="pre_input",
                scope="coordinator",
            ),
        ]
        should_proceed, rejection, results = await evaluate_pre_input(
            agent_id="worker-agent",
            user_input="off-topic question",
            bindings=scoped_bindings,
            governance=failing_gov,
        )
        assert should_proceed is True
        assert rejection is None
        assert results == []
