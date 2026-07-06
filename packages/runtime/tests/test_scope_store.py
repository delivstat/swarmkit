"""Unit tests for the ScopeStore — the single scope.json reader/writer (PR-K1).

Guards the two bugs it fixes: (1) create/update keep solution_approach + open_questions (the
_task_plan_handler write path used to drop them), and (2) all paths agree on the run-scoped path.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime.langgraph_compiler._run_context import run_context, run_state_dir
from swarmkit_runtime.langgraph_compiler._scope import (
    create_scope,
    read_scope,
    scope_exists,
    scope_path,
    update_scope,
)
from swarmkit_runtime.langgraph_compiler._task_plan_handler import _write_scope

_CREATE_ARGS = {
    "source": "spec.md",
    "requirements": ["r1", "r2"],
    "constraints": ["c1"],
    "solution_approach": ["approach-a"],
    "open_questions": ["q1"],
}


def test_create_keeps_every_field(tmp_path: Path) -> None:
    with run_context(tmp_path, "run-1"):
        scope = create_scope(_CREATE_ARGS)
        assert scope["source"] == "spec.md"
        assert scope["requirements"] == ["r1", "r2"]
        # The two fields the drifted writer dropped:
        assert scope["solution_approach"] == ["approach-a"]
        assert scope["open_questions"] == ["q1"]
        assert scope_path() == run_state_dir() / "scope.json"
        assert read_scope() == scope  # round-trips from disk


def test_update_is_additive_including_design_fields(tmp_path: Path) -> None:
    with run_context(tmp_path, "run-1"):
        create_scope(_CREATE_ARGS)
        updated = update_scope(
            {
                "add_requirements": ["r3"],
                "add_solution_approach": ["approach-b"],
                "add_open_questions": ["q2"],
            }
        )
        assert updated is not None
        assert updated["requirements"] == ["r1", "r2", "r3"]
        assert updated["solution_approach"] == ["approach-a", "approach-b"]
        assert updated["open_questions"] == ["q1", "q2"]


def test_update_without_scope_returns_none(tmp_path: Path) -> None:
    with run_context(tmp_path, "run-1"):
        assert not scope_exists()
        assert update_scope({"add_requirements": ["x"]}) is None


def test_handler_write_path_keeps_design_fields(tmp_path: Path) -> None:
    # Regression: _task_plan_handler._write_scope dropped solution_approach/open_questions.

    with run_context(tmp_path, "run-1"):
        _write_scope(_CREATE_ARGS, "root")
        scope = read_scope()
        assert scope is not None
        assert scope["solution_approach"] == ["approach-a"]
        assert scope["open_questions"] == ["q1"]


def test_isolated_run_dirs_do_not_share_scope(tmp_path: Path) -> None:
    with run_context(tmp_path, "run-a"):
        create_scope({"source": "a", "requirements": ["ra"]})
    with run_context(tmp_path, "run-b"):
        assert read_scope() is None  # different run → its own (empty) scope
        create_scope({"source": "b", "requirements": ["rb"]})
    with run_context(tmp_path, "run-a"):
        scope = read_scope()
        assert scope is not None and scope["requirements"] == ["ra"]  # run-a's scope intact
