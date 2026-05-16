"""Tests for decision gate trigger points in the compiler."""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._decision_gate import (
    evaluate_checkpoint,
    evaluate_post_output,
    evaluate_pre_synthesis,
    format_gate_feedback,
)


@pytest.fixture
def passing_gov() -> MockGovernanceProvider:
    return MockGovernanceProvider(allow_all=True)


@pytest.fixture
def failing_gov() -> MockGovernanceProvider:
    return MockGovernanceProvider(
        allow_all=True,
        decision_skill_verdicts={
            "grounding-verifier": DecisionSkillResult(
                skill_id="grounding-verifier",
                verdict="fail",
                confidence=0.8,
                reasoning="Found fabricated API name 'OrderService'",
                flagged_items=["OrderService is not in any tool result"],
            ),
        },
    )


@pytest.fixture
def bindings() -> list[DecisionSkillBinding]:
    return [
        DecisionSkillBinding(id="grounding-verifier", trigger="post_output"),
        DecisionSkillBinding(id="contradiction-detector", trigger="pre_synthesis"),
        DecisionSkillBinding(id="checkpoint-verifier", trigger="checkpoint"),
    ]


class TestPostOutput:
    @pytest.mark.asyncio
    async def test_passes_through_when_no_bindings(
        self, passing_gov: MockGovernanceProvider
    ) -> None:
        output, results = await evaluate_post_output(
            agent_id="test-agent",
            output="some output",
            bindings=[],
            governance=passing_gov,
        )
        assert output == "some output"
        assert results == []

    @pytest.mark.asyncio
    async def test_passes_through_when_verdict_pass(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        output, results = await evaluate_post_output(
            agent_id="test-agent",
            output="some output",
            bindings=bindings,
            governance=passing_gov,
        )
        assert output == "some output"
        assert len(results) == 1
        assert results[0].verdict == "pass"

    @pytest.mark.asyncio
    async def test_annotates_output_on_fail(
        self,
        failing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        output, results = await evaluate_post_output(
            agent_id="test-agent",
            output="The OrderService handles returns",
            bindings=bindings,
            governance=failing_gov,
        )
        assert "GOVERNANCE FLAGS" in output
        assert "OrderService is not in any tool result" in output
        assert results[0].verdict == "fail"

    @pytest.mark.asyncio
    async def test_scope_filtering(
        self,
        failing_gov: MockGovernanceProvider,
    ) -> None:
        scoped_bindings = [
            DecisionSkillBinding(
                id="grounding-verifier",
                trigger="post_output",
                scope="jira-researcher",
            ),
        ]
        output, results = await evaluate_post_output(
            agent_id="config-analyst",
            output="some output",
            bindings=scoped_bindings,
            governance=failing_gov,
        )
        assert output == "some output"
        assert results == []


class TestCheckpoint:
    @pytest.mark.asyncio
    async def test_no_applicable_bindings(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        results = await evaluate_checkpoint(
            agent_id="non-matching-agent",
            task_results={"task-1": "result"},
            bindings=[
                DecisionSkillBinding(
                    id="checkpoint-verifier",
                    trigger="checkpoint",
                    scope="other-agent",
                ),
            ],
            governance=passing_gov,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_fires_for_matching_agent(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        results = await evaluate_checkpoint(
            agent_id="test-agent",
            task_results={"task-1": "result one", "task-2": "result two"},
            bindings=bindings,
            governance=passing_gov,
        )
        assert len(results) == 1
        assert results[0].verdict == "pass"


class TestPreSynthesis:
    @pytest.mark.asyncio
    async def test_fires_for_matching_binding(
        self,
        passing_gov: MockGovernanceProvider,
        bindings: list[DecisionSkillBinding],
    ) -> None:
        results = await evaluate_pre_synthesis(
            agent_id="test-agent",
            task_results={"task-1": "result"},
            original_input="What is X?",
            bindings=bindings,
            governance=passing_gov,
        )
        assert len(results) == 1
        assert results[0].verdict == "pass"


class TestFormatGateFeedback:
    def test_empty_when_all_pass(self) -> None:
        results = [
            DecisionSkillResult(skill_id="test", verdict="pass", confidence=1.0, reasoning="ok"),
        ]
        assert format_gate_feedback(results) == ""

    def test_formats_failures(self) -> None:
        results = [
            DecisionSkillResult(
                skill_id="grounding-verifier",
                verdict="fail",
                confidence=0.8,
                reasoning="Unsourced claims found",
                flagged_items=["Claim A", "Claim B"],
            ),
        ]
        feedback = format_gate_feedback(results)
        assert "GOVERNANCE FEEDBACK" in feedback
        assert "grounding-verifier" in feedback
        assert "Claim A" in feedback
        assert "Claim B" in feedback
