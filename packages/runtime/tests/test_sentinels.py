"""Status enums + routing sentinels — the string contract that keeps the refactor safe.

The compiler writes these into ``state['agent_results']`` and ``tasks.json``; existing checkpoints
and persisted plans hold the bare strings. Because both enums are ``StrEnum``, a member must compare
equal to and serialise as its literal — otherwise a resumed run or a reloaded plan would misroute.
"""

from __future__ import annotations

import json

from swarmkit_runtime.langgraph_compiler._sentinels import (
    TASK_PLAN_ACTIVE,
    AgentStatus,
    TaskStatus,
    delegated_child,
    is_delegated,
    is_task_plan_status,
    make_delegated,
)
from swarmkit_runtime.langgraph_compiler._task_plan import Task, TaskPlan


def test_enum_members_equal_their_literals() -> None:
    # The exact strings persisted before this refactor — must not drift. Asserting on ``.value``
    # (rather than ``member == "literal"``, which mypy rejects as non-overlapping) both satisfies
    # the type checker and proves the member reconstructs from the bare persisted string.
    for member, literal in [
        (TaskStatus.PENDING, "pending"),
        (TaskStatus.IN_PROGRESS, "in_progress"),
        (TaskStatus.COMPLETED, "completed"),
        (TaskStatus.FAILED, "failed"),
        (AgentStatus.TASK_PLAN_CREATED, "__task_plan_created__"),
        (AgentStatus.TASK_PLAN_UPDATED, "__task_plan_updated__"),
        (AgentStatus.TASK_PLAN_EXECUTING, "__task_plan_executing__"),
        (AgentStatus.TASK_PLAN_COMPLETE, "__task_plan_complete__"),
        (AgentStatus.TASK_PLAN_BLOCKED, "__task_plan_blocked__"),
        (AgentStatus.DELEGATED_PARALLEL, "__delegated_parallel__"),
        (AgentStatus.DONE, "__done__"),
    ]:
        assert member.value == literal
        assert type(member)(literal) is member  # bare string → the same enum member


def test_str_enum_json_serialises_as_literal() -> None:
    assert json.dumps({"s": AgentStatus.DONE}) == '{"s": "__done__"}'
    assert json.dumps({"s": TaskStatus.COMPLETED}) == '{"s": "completed"}'


def test_delegation_helpers() -> None:
    marker = make_delegated("worker-1")
    assert marker == "__delegated__:worker-1"
    assert is_delegated(marker)
    assert delegated_child(marker) == "worker-1"
    # non-delegation values (incl. non-strings) are rejected.
    assert not is_delegated(AgentStatus.DONE)
    assert not is_delegated(None)
    assert not is_delegated("plain text")


def test_task_plan_status_predicate_and_active_set() -> None:
    assert is_task_plan_status(AgentStatus.TASK_PLAN_CREATED)
    assert is_task_plan_status("__task_plan_executing__")  # bare string from an old checkpoint
    assert not is_task_plan_status(AgentStatus.DELEGATED_PARALLEL)
    assert not is_task_plan_status(make_delegated("x"))
    # ACTIVE = the markers that re-enter the coordinator; complete/blocked are terminal.
    assert AgentStatus.TASK_PLAN_CREATED in TASK_PLAN_ACTIVE
    assert AgentStatus.TASK_PLAN_COMPLETE not in TASK_PLAN_ACTIVE
    # membership works for the bare string form too (an old checkpoint's raw value routes).
    assert AgentStatus("__task_plan_updated__") in TASK_PLAN_ACTIVE


def test_task_status_round_trips_through_json() -> None:
    plan = TaskPlan(tasks=[Task(id="t1", agent="self", instruction="do it")])
    plan.mark_started("t1")
    plan.mark_completed("t1")
    reloaded = TaskPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert reloaded.tasks[0].status == TaskStatus.COMPLETED
    assert isinstance(reloaded.tasks[0].status, TaskStatus)
