"""Tests for the TaskPlan structured delegation module."""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime.langgraph_compiler._task_plan import TaskPlan
from swarmkit_runtime.langgraph_compiler._task_plan_handler import _load_plan


class TestTaskPlan:
    def test_add_tasks(self) -> None:
        plan = TaskPlan()
        errors = plan.add_tasks(
            [
                {"id": "t1", "agent": "worker-a", "instruction": "do stuff"},
                {"id": "t2", "agent": "worker-b", "instruction": "do more", "depends_on": ["t1"]},
            ]
        )
        assert not errors
        assert len(plan.tasks) == 2
        assert plan.tasks[1].depends_on == ["t1"]

    def test_add_tasks_rejects_duplicates(self) -> None:
        plan = TaskPlan()
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "x"}])
        errors = plan.add_tasks([{"id": "t1", "agent": "b", "instruction": "y"}])
        assert any("Duplicate" in e for e in errors)
        assert len(plan.tasks) == 1

    def test_add_tasks_rejects_missing_fields(self) -> None:
        plan = TaskPlan()
        errors = plan.add_tasks(
            [
                {"id": "", "agent": "a", "instruction": "x"},
                {"id": "t1", "agent": "", "instruction": "x"},
                {"id": "t2", "agent": "a", "instruction": ""},
            ]
        )
        assert len(errors) == 3
        assert len(plan.tasks) == 0

    def test_validate_agents(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "worker-a", "instruction": "x"},
                {"id": "t2", "agent": "self", "instruction": "y"},
                {"id": "t3", "agent": "unknown", "instruction": "z"},
            ]
        )
        errors = plan.validate_agents({"worker-a", "worker-b"})
        assert len(errors) == 1
        assert "unknown" in errors[0]

    def test_validate_dependencies(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "x"},
                {"id": "t2", "agent": "b", "instruction": "y", "depends_on": ["t1", "missing"]},
                {"id": "t3", "agent": "c", "instruction": "z", "depends_on": ["t3"]},
            ]
        )
        errors = plan.validate_dependencies()
        assert any("missing" in e for e in errors)
        assert any("depends on itself" in e for e in errors)

    def test_get_runnable_tasks(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "x"},
                {"id": "t2", "agent": "b", "instruction": "y"},
                {"id": "t3", "agent": "c", "instruction": "z", "depends_on": ["t1", "t2"]},
            ]
        )
        runnable = plan.get_runnable_tasks()
        assert {t.id for t in runnable} == {"t1", "t2"}

        plan.mark_completed("t1")
        runnable = plan.get_runnable_tasks()
        assert {t.id for t in runnable} == {"t2"}

        plan.mark_completed("t2")
        runnable = plan.get_runnable_tasks()
        assert {t.id for t in runnable} == {"t3"}

    def test_mark_lifecycle(self) -> None:
        plan = TaskPlan()
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "x"}])

        plan.mark_started("t1")
        assert plan.tasks[0].status == "in_progress"
        assert plan.tasks[0].started_at is not None

        plan.mark_completed("t1", key_findings=["found stuff"], tool_calls=10)
        assert plan.tasks[0].status == "completed"
        assert plan.tasks[0].key_findings == ["found stuff"]
        assert plan.tasks[0].tool_calls == 10

    def test_mark_failed(self) -> None:
        plan = TaskPlan()
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "x"}])
        plan.mark_started("t1")
        plan.mark_failed("t1", "model not found")
        assert plan.tasks[0].status == "failed"
        assert plan.tasks[0].error == "model not found"

    def test_remove_tasks(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "x"},
                {"id": "t2", "agent": "b", "instruction": "y"},
            ]
        )
        plan.mark_started("t1")
        errors = plan.remove_tasks(["t1", "t2", "t3"])
        assert any("status is" in e for e in errors)
        assert any("not found" in e for e in errors)
        assert len(plan.tasks) == 1
        assert plan.tasks[0].id == "t1"

    def test_update_tasks(self) -> None:
        plan = TaskPlan()
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "original"}])
        errors = plan.update_tasks([{"id": "t1", "instruction": "updated"}])
        assert not errors
        assert plan.tasks[0].instruction == "updated"

    def test_update_rejects_non_pending(self) -> None:
        plan = TaskPlan()
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "x"}])
        plan.mark_started("t1")
        errors = plan.update_tasks([{"id": "t1", "instruction": "new"}])
        assert any("Cannot update" in e for e in errors)

    def test_all_done(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "x"},
                {"id": "t2", "agent": "b", "instruction": "y"},
            ]
        )
        assert not plan.all_done()
        plan.mark_completed("t1")
        assert not plan.all_done()
        plan.mark_failed("t2", "error")
        assert plan.all_done()

    def test_render_status(self) -> None:
        plan = TaskPlan()
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "x"},
                {"id": "t2", "agent": "b", "instruction": "y", "depends_on": ["t1"]},
            ]
        )
        plan.mark_completed("t1", key_findings=["found stuff"])
        status = plan.render_status()
        assert "completed: t1" in status
        assert "found stuff" in status
        assert "pending: t2" in status

    def test_save_and_load(self, tmp_path: Path) -> None:
        plan = TaskPlan(run_id="test-123", topology="test-topo", created_at=1000.0)
        plan.add_tasks(
            [
                {"id": "t1", "agent": "a", "instruction": "do stuff"},
                {"id": "t2", "agent": "b", "instruction": "more stuff", "depends_on": ["t1"]},
            ]
        )
        plan.mark_completed("t1", key_findings=["found it"], result_path="/tmp/t1.md")

        plan.save(tmp_path)
        loaded = TaskPlan.load(tmp_path / "tasks.json")

        assert loaded.run_id == "test-123"
        assert loaded.topology == "test-topo"
        assert len(loaded.tasks) == 2
        assert loaded.tasks[0].status == "completed"
        assert loaded.tasks[0].key_findings == ["found it"]
        assert loaded.tasks[1].depends_on == ["t1"]

    def test_to_dict(self) -> None:
        plan = TaskPlan(run_id="r1")
        plan.add_tasks([{"id": "t1", "agent": "a", "instruction": "x"}])
        d = plan.to_dict()
        assert d["run_id"] == "r1"
        assert len(d["tasks"]) == 1
        assert d["tasks"][0]["id"] == "t1"


_FULL_PLAN = {
    "run_id": "r1",
    "topology": "hello",
    "created_at": 12.5,
    "tasks": [
        {
            "id": "t1",
            "agent": "self",
            "instruction": "do",
            "depends_on": ["t0"],
            "status": "completed",
            "started_at": 1.0,
            "completed_at": 3.0,
            "duration_s": 2.0,
            "tool_calls": 4,
            "key_findings": ["a"],
            "result_path": "/p",
            "error": None,
            "delegation_count": 1,
        }
    ],
}


class TestFromDict:
    """TaskPlan.from_dict / Task.from_dict — the single dict→object loader (PR-K2)."""

    def test_roundtrip_preserves_all_fields(self) -> None:
        plan = TaskPlan.from_dict(_FULL_PLAN)
        assert plan.run_id == "r1" and plan.topology == "hello" and plan.created_at == 12.5
        t = plan.tasks[0]
        # The timing/tool-call fields the drifted _load_plan used to drop:
        assert t.started_at == 1.0 and t.completed_at == 3.0
        assert t.duration_s == 2.0 and t.tool_calls == 4
        assert t.status == "completed" and t.depends_on == ["t0"]
        # to_dict is the inverse.
        assert TaskPlan.from_dict(plan.to_dict()).tasks[0] == t

    def test_empty_and_none(self) -> None:
        assert TaskPlan.from_dict({}).tasks == []
        assert TaskPlan.from_dict(None).tasks == []

    def test_defaults_for_minimal_task(self) -> None:
        t = TaskPlan.from_dict({"tasks": [{"id": "x", "agent": "self"}]}).tasks[0]
        assert t.status == "pending" and t.tool_calls == 0 and t.depends_on == []

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        plan = TaskPlan.from_dict(_FULL_PLAN)
        plan.save(tmp_path)
        loaded = TaskPlan.load(tmp_path / "tasks.json")
        assert loaded.tasks[0].tool_calls == 4 and loaded.tasks[0].duration_s == 2.0

    def test_handler_load_plan_keeps_timing(self) -> None:
        # Regression: _task_plan_handler._load_plan dropped these before PR-K2.

        plan = _load_plan(_FULL_PLAN)
        assert plan.tasks[0].started_at == 1.0 and plan.tasks[0].tool_calls == 4
