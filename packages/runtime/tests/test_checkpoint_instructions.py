"""Tests for structured checkpoint instructions (JSON action specs).

Verifies that checkpoint prompts are structured JSON instead of prose,
and that the coordinator receives machine-parseable instructions.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.langgraph_compiler._prompts import (
    _build_checkpoint_spec,
    _build_prompt_messages,
)
from swarmkit_runtime.langgraph_compiler._state import PlanningConfig, SwarmState
from swarmkit_runtime.resolver._resolved import ResolvedAgent


def _make_coordinator() -> ResolvedAgent:
    return ResolvedAgent(
        id="coordinator",
        role="root",  # type: ignore[arg-type]
        model={"name": "mock"},
        prompt={"system": "You are a coordinator."},
        skills=(),
        iam=None,
        children=(
            ResolvedAgent(
                id="researcher",
                role="worker",  # type: ignore[arg-type]
                model={"name": "mock"},
                prompt=None,
                skills=(),
                iam=None,
            ),
            ResolvedAgent(
                id="analyst",
                role="worker",  # type: ignore[arg-type]
                model={"name": "mock"},
                prompt=None,
                skills=(),
                iam=None,
            ),
        ),
    )


# ---- _build_checkpoint_spec ----------------------------------------------


class TestBuildCheckpointSpec:
    def test_produces_valid_json(self) -> None:
        spec = _build_checkpoint_spec(
            phase="synthesis",
            plan_status="all_done",
            scope_status="exists",
            original_request="Analyze the system",
            required_actions=[
                {"tool": "read-scope", "reason": "verify scope"},
                {"tool": "write_response", "reason": "final answer"},
            ],
        )
        parsed = json.loads(spec)
        assert parsed["checkpoint"]["phase"] == "synthesis"
        assert parsed["checkpoint"]["plan_status"] == "all_done"
        assert parsed["checkpoint"]["scope_status"] == "exists"
        assert len(parsed["checkpoint"]["required_actions"]) == 2
        assert parsed["original_request"] == "Analyze the system"

    def test_research_complete_phase(self) -> None:
        spec = _build_checkpoint_spec(
            phase="research_complete",
            plan_status="all_done",
            scope_status="not_created",
            required_actions=[
                {"tool": "read-task-result", "reason": "read findings"},
                {"tool": "create-scope", "reason": "define scope"},
            ],
        )
        parsed = json.loads(spec)
        assert parsed["checkpoint"]["phase"] == "research_complete"
        assert parsed["checkpoint"]["scope_status"] == "not_created"

    def test_in_progress_phase(self) -> None:
        spec = _build_checkpoint_spec(
            phase="in_progress",
            plan_status="tasks_remaining",
            required_actions=[
                {"tool": "update-task-plan", "reason": "modify tasks"},
                {"tool": "wait", "reason": "pending tasks run automatically"},
            ],
        )
        parsed = json.loads(spec)
        assert parsed["checkpoint"]["phase"] == "in_progress"
        assert parsed["checkpoint"]["plan_status"] == "tasks_remaining"

    def test_default_scope_status(self) -> None:
        spec = _build_checkpoint_spec(
            phase="test",
            plan_status="test",
            required_actions=[],
        )
        parsed = json.loads(spec)
        assert parsed["checkpoint"]["scope_status"] == "not_created"


# ---- _build_prompt_messages with task plan state -------------------------


def _make_plan_state(
    *,
    all_done: bool = False,
    input_text: str = "Analyze the code",
    include_self_task: bool = False,
) -> SwarmState:
    """Build a SwarmState with a task plan."""
    tasks: list[dict[str, Any]] = [
        {
            "id": "research",
            "agent": "researcher",
            "instruction": "Research the codebase",
            "status": "completed" if all_done else "pending",
            "key_findings": ["Found X", "Found Y"] if all_done else [],
            "result_path": "/tmp/research.md" if all_done else None,
        },
        {
            "id": "analysis",
            "agent": "analyst",
            "instruction": "Analyze findings",
            "status": "completed" if all_done else "pending",
            "key_findings": ["Conclusion A"] if all_done else [],
            "result_path": "/tmp/analysis.md" if all_done else None,
        },
    ]
    if include_self_task:
        tasks.append(
            {
                "id": "synthesis",
                "agent": "self",
                "instruction": "Write final answer",
                "status": "completed" if all_done else "pending",
                "key_findings": [],
                "result_path": None,
            }
        )
    return {
        "input": input_text,
        "messages": [],
        "agent_results": {"coordinator": "__task_plan_executing__"},
        "delegation_counts": {},
        "task_plan": {
            "run_id": "test-run",
            "topology": "test-topo",
            "created_at": 0.0,
            "tasks": tasks,
        },
        "current_agent": "coordinator",
        "output": "",
    }


class TestCheckpointMessages:
    def test_all_done_synthesis_is_json(self) -> None:
        agent = _make_coordinator()
        state = _make_plan_state(all_done=True, include_self_task=True)
        messages = _build_prompt_messages(agent, state)

        assert len(messages) == 1
        content = messages[0].content
        assert isinstance(content, str)
        assert "TASK PLAN STATUS" in content

        json_start = content.index("{")
        spec = json.loads(content[json_start:])
        assert spec["checkpoint"]["phase"] == "synthesis"
        assert spec["checkpoint"]["plan_status"] == "all_done"
        assert any(a["tool"] == "write_response" for a in spec["checkpoint"]["required_actions"])

    def test_in_progress_is_json(self) -> None:
        agent = _make_coordinator()
        state = _make_plan_state(all_done=False)
        messages = _build_prompt_messages(agent, state)

        assert len(messages) == 1
        content = messages[0].content
        assert isinstance(content, str)

        json_start = content.index("{")
        spec = json.loads(content[json_start:])
        assert spec["checkpoint"]["phase"] == "in_progress"
        assert spec["checkpoint"]["plan_status"] == "tasks_remaining"

    def test_all_done_with_scope_required_is_json(self) -> None:
        agent = _make_coordinator()
        state = _make_plan_state(all_done=True, include_self_task=True)
        planning = PlanningConfig(scope_required=True)
        messages = _build_prompt_messages(agent, state, planning_config=planning)

        assert len(messages) == 1
        content = messages[0].content
        assert isinstance(content, str)

        json_start = content.index("{")
        spec = json.loads(content[json_start:])
        assert spec["checkpoint"]["phase"] == "scope_required"
        assert any(a["tool"] == "create-scope" for a in spec["checkpoint"]["required_actions"])

    def test_two_phase_research_complete_is_json(self) -> None:
        agent = _make_coordinator()
        state = _make_plan_state(all_done=True)
        planning = PlanningConfig(two_phase=True)
        messages = _build_prompt_messages(agent, state, planning_config=planning)

        assert len(messages) == 1
        content = messages[0].content
        assert isinstance(content, str)

        json_start = content.index("{")
        spec = json.loads(content[json_start:])
        assert spec["checkpoint"]["phase"] == "research_complete"
        assert any(a["tool"] == "create-scope" for a in spec["checkpoint"]["required_actions"])

    def test_checkpoint_contains_original_request(self) -> None:
        agent = _make_coordinator()
        state = _make_plan_state(all_done=True, input_text="Find all bugs", include_self_task=True)
        messages = _build_prompt_messages(agent, state)

        content = messages[0].content
        assert isinstance(content, str)
        json_start = content.index("{")
        spec = json.loads(content[json_start:])
        assert spec["original_request"] == "Find all bugs"

    def test_no_prose_instructions_in_checkpoint(self) -> None:
        """Checkpoint messages should not contain English instruction prose."""
        agent = _make_coordinator()
        for all_done in [True, False]:
            state = _make_plan_state(all_done=all_done)
            messages = _build_prompt_messages(agent, state)
            content = messages[0].content
            assert isinstance(content, str)
            assert "You MUST now" not in content
            assert "You can:" not in content
            assert "Synthesize the findings" not in content
            assert "Review the results above" not in content
