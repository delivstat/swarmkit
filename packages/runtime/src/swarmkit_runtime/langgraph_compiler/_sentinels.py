"""Status enums + routing sentinels for structured delegation.

Two families of magic strings used to be scattered across the compiler as bare literals and
``startswith("__task_plan_")`` / ``startswith("__delegated__:")`` checks:

* **Task lifecycle** (``TaskStatus``) — the ``status`` field of a :class:`Task` in a delegation
  plan, persisted in ``tasks.json`` (``pending`` → ``in_progress`` → ``completed``/``failed``).
* **Agent-result routing markers** (``AgentStatus`` + the delegation helpers) — control values
  written to ``state['agent_results'][agent_id]`` that the compiler's conditional edges read to pick
  the next node. These are markers, never model output.

Both are ``StrEnum``, so members compare equal to and JSON-serialise as their string value —
existing state dicts, checkpoints, and ``tasks.json`` files round-trip unchanged. The parametric
"delegated to child X" marker can't be an enum member (X varies), so it lives as a prefix constant
plus :func:`make_delegated` / :func:`is_delegated` / :func:`delegated_child`.
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """Lifecycle status of a :class:`Task` in a delegation plan (persisted in ``tasks.json``)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStatus(StrEnum):
    """Sentinel values written to ``state['agent_results'][agent_id]`` to drive routing.

    The ``TASK_PLAN_*`` members share the :data:`TASK_PLAN_PREFIX`; the coordinator re-enters while
    an ``ACTIVE`` marker is set. ``DONE`` is the terminal router destination. The parametric
    "delegated to a specific child" marker is not a member here — see :func:`make_delegated`.
    """

    TASK_PLAN_CREATED = "__task_plan_created__"
    TASK_PLAN_UPDATED = "__task_plan_updated__"
    TASK_PLAN_EXECUTING = "__task_plan_executing__"
    TASK_PLAN_COMPLETE = "__task_plan_complete__"
    TASK_PLAN_BLOCKED = "__task_plan_blocked__"
    DELEGATED_PARALLEL = "__delegated_parallel__"
    DONE = "__done__"


# Shared prefix of the TASK_PLAN_* markers (created/updated/executing/complete/blocked).
TASK_PLAN_PREFIX = "__task_plan_"

# Markers meaning "the plan still has work — re-enter the coordinator to run the next batch".
TASK_PLAN_ACTIVE: tuple[AgentStatus, ...] = (
    AgentStatus.TASK_PLAN_CREATED,
    AgentStatus.TASK_PLAN_UPDATED,
    AgentStatus.TASK_PLAN_EXECUTING,
)

# Prefix of the parametric "this agent delegated to child <id>" marker.
DELEGATED_PREFIX = "__delegated__:"


def is_task_plan_status(value: object) -> bool:
    """True if *value* is any ``TASK_PLAN_*`` agent-result marker."""
    return isinstance(value, str) and value.startswith(TASK_PLAN_PREFIX)


def make_delegated(child_id: str) -> str:
    """The agent-result marker meaning "this agent delegated to *child_id*"."""
    return f"{DELEGATED_PREFIX}{child_id}"


def is_delegated(value: object) -> bool:
    """True if *value* is a "delegated to child" marker (see :func:`make_delegated`)."""
    return isinstance(value, str) and value.startswith(DELEGATED_PREFIX)


def delegated_child(value: str) -> str:
    """Extract the child id from a delegated marker (caller ensures :func:`is_delegated`)."""
    return value.split(":", 1)[1]


__all__ = [
    "DELEGATED_PREFIX",
    "TASK_PLAN_ACTIVE",
    "TASK_PLAN_PREFIX",
    "AgentStatus",
    "TaskStatus",
    "delegated_child",
    "is_delegated",
    "is_task_plan_status",
    "make_delegated",
]
