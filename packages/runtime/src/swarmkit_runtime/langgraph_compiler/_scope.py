"""ScopeStore — the one reader/writer of a run's frozen scope contract (``scope.json``).

``create-scope`` / ``update-scope`` / ``read-scope`` were answered by two handlers (``_tool_loop``
and ``_task_plan_handler``) with divergent schemas: the ``_task_plan_handler`` pair silently dropped
``solution_approach`` + ``open_questions``, and ``_decision_gate`` read a stale ``current/`` path
(broken once run-state became run-id-scoped). This module is the single source of truth — all
fields, one run-scoped path (``run_state_dir()/scope.json``) — that every dispatch + reader routes
through, so the scope can no longer diverge by code path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The additive list fields of the scope contract (``update-scope`` merges ``add_<field>`` into
# each). ``source`` is a scalar set only at create time.
SCOPE_LIST_FIELDS = (
    "requirements",
    "constraints",
    "authoritative_sources",
    "excluded",
    "decisions",
    "related",
    "solution_approach",
    "open_questions",
)


def scope_path() -> Path:
    from swarmkit_runtime.langgraph_compiler._run_context import run_state_dir  # noqa: PLC0415

    return run_state_dir() / "scope.json"


def scope_exists() -> bool:
    return scope_path().exists()


def read_scope() -> dict[str, Any] | None:
    """The current scope dict, or None if no scope has been created for this run."""
    path = scope_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, ValueError):
        return None


def create_scope(args: dict[str, Any]) -> dict[str, Any]:
    """Write a fresh scope from ``create-scope`` args (scalar source + every list field)."""
    scope: dict[str, Any] = {"source": args.get("source", "")}
    for field in SCOPE_LIST_FIELDS:
        scope[field] = args.get(field, [])
    _write(scope)
    return scope


def update_scope(args: dict[str, Any]) -> dict[str, Any] | None:
    """Additively merge ``update-scope`` args (``add_<field>``) into the existing scope.

    Returns the updated scope, or None if no scope exists yet (caller reports the error).
    """
    scope = read_scope()
    if scope is None:
        return None
    for field in SCOPE_LIST_FIELDS:
        items = args.get(f"add_{field}", [])
        if items:
            scope[field] = scope.get(field, []) + items
    _write(scope)
    return scope


def _write(scope: dict[str, Any]) -> None:
    scope_path().write_text(json.dumps(scope, indent=2), encoding="utf-8")
