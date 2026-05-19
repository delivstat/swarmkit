"""Task-plan tool handling (create / update / read-result).

Extracted from ``_dispatch_response`` to keep delegation module lean.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

from swarmkit_runtime.model_providers import CompletionResponse
from swarmkit_runtime.resolver import ResolvedAgent

from ._helpers import _progress


def _handle_task_plan_tools(  # noqa: PLR0912, PLR0915
    response: CompletionResponse,
    agent: ResolvedAgent,
    agent_id: str,
    state: Any,
    planning_config: Any = None,
    synthesis_config: Any = None,
) -> dict[str, Any] | None:
    """Handle planning tools in a model response (two-pass).

    Pass 1: execute side-effect-only tools (read-task-result, create-scope, update-scope).
    Pass 2: execute state-changing tools (create-task-plan, update-task-plan).

    Returns a state dict if a state-changing tool was called, None otherwise.
    Side-effect tools always run regardless of whether a state change follows.
    """

    _STATE_TOOLS = {"create-task-plan", "update-task-plan"}
    _SIDE_EFFECT_TOOLS = {"read-task-result", "create-scope", "update-scope", "read-scope"}

    has_state_tool = any(
        hasattr(b, "tool_name") and b.tool_name in _STATE_TOOLS for b in response.content
    )
    if not has_state_tool:
        has_side_effect = any(
            hasattr(b, "tool_name") and b.tool_name in _SIDE_EFFECT_TOOLS for b in response.content
        )
        if not has_side_effect:
            return None

    # Pass 1: side-effect tools (run before state changes)
    for block in response.content:
        if not hasattr(block, "tool_name") or not block.tool_name:
            continue
        if block.tool_name not in _SIDE_EFFECT_TOOLS:
            continue

        args = _parse_args(block)

        if block.tool_name == "create-scope":
            _write_scope(args, agent_id)
        elif block.tool_name == "update-scope":
            _update_scope(args, agent_id)

    # If no state-changing tools, return None (tool loop will handle the rest)
    if not has_state_tool:
        return None

    # Pass 2: state-changing tools
    existing_plan_data = (state or {}).get("task_plan", {})
    plan = _load_plan(existing_plan_data)

    for block in response.content:
        if not hasattr(block, "tool_name") or not block.tool_name:
            continue
        if block.tool_name not in _STATE_TOOLS:
            continue

        args = _parse_args(block)

        if block.tool_name == "create-task-plan":
            raw_tasks = args.get("tasks", [])
            if not raw_tasks:
                continue

            raw_tasks = _enforce_two_phase(raw_tasks, planning_config, agent_id)

            import time as _time  # noqa: PLC0415

            plan.run_id = (state or {}).get("input", "")[:50]
            plan.created_at = _time.time()
            errors = plan.add_tasks(raw_tasks)
            valid_agents = {c.id for c in agent.children}
            errors.extend(plan.validate_agents(valid_agents))
            fixes = plan.auto_fix_dependencies()

            _synth_model: str = getattr(synthesis_config, "model", "") if synthesis_config else ""
            if _synth_model:
                before = len(plan.tasks)
                plan.tasks = [t for t in plan.tasks if t.id != "__auto_synthesize__"]
                if len(plan.tasks) < before:
                    fixes.append(
                        "Removed __auto_synthesize__: synthesis config will "
                        "handle document generation automatically"
                    )

            errors.extend(plan.validate_dependencies())

            task_count = len(plan.tasks)
            _progress(f"[{agent_id}] created task plan: {task_count} tasks")
            for fix in fixes:
                _progress(f"  {fix}")
            for task in plan.tasks:
                deps = f" (after: {', '.join(task.depends_on)})" if task.depends_on else ""
                _progress(f"  - {task.id} -> {task.agent}{deps}")
            if errors:
                _progress(f"  warnings: {'; '.join(errors)}")

            return {
                "current_agent": agent_id,
                "task_plan": plan.to_dict(),
                "agent_results": {agent_id: "__task_plan_created__"},
                "messages": [
                    AIMessage(
                        content=f"[{agent_id}] Task plan created with {task_count} tasks.",
                        name=agent_id,
                    ),
                ],
            }

        if block.tool_name == "update-task-plan":
            upd_errors: list[str] = []
            if args.get("add"):
                upd_errors.extend(plan.add_tasks(args["add"]))
                valid_agents = {c.id for c in agent.children}
                upd_errors.extend(plan.validate_agents(valid_agents))
            if args.get("remove"):
                upd_errors.extend(plan.remove_tasks(args["remove"]))
            if args.get("update"):
                upd_errors.extend(plan.update_tasks(args["update"]))
            fixes = plan.auto_fix_dependencies()
            upd_errors.extend(plan.validate_dependencies())

            pending = sum(1 for t in plan.tasks if t.status == "pending")
            _progress(f"[{agent_id}] updated task plan: {len(plan.tasks)} total, {pending} pending")
            for fix in fixes:
                _progress(f"  {fix}")
            if upd_errors:
                _progress(f"  warnings: {'; '.join(upd_errors)}")

            if args.get("complete"):
                return {
                    "current_agent": agent_id,
                    "task_plan": plan.to_dict(),
                    "agent_results": {agent_id: "__task_plan_complete__"},
                    "messages": [
                        AIMessage(
                            content=f"[{agent_id}] Task plan marked complete. Synthesizing.",
                            name=agent_id,
                        ),
                    ],
                }

            return {
                "current_agent": agent_id,
                "task_plan": plan.to_dict(),
                "agent_results": {agent_id: "__task_plan_updated__"},
                "messages": [
                    AIMessage(
                        content=f"[{agent_id}] Task plan updated. {pending} tasks pending.",
                        name=agent_id,
                    ),
                ],
            }

    return None


def _parse_args(block: Any) -> dict[str, Any]:
    """Extract args dict from a tool call block."""
    args = block.tool_input
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    return args


def _load_plan(plan_data: dict[str, Any]) -> Any:
    """Load a TaskPlan from state data."""
    from swarmkit_runtime.langgraph_compiler._task_plan import Task, TaskPlan  # noqa: PLC0415

    if plan_data and plan_data.get("tasks"):
        plan = TaskPlan(
            run_id=plan_data.get("run_id", ""),
            topology=plan_data.get("topology", ""),
            created_at=plan_data.get("created_at", 0.0),
        )
        for raw in plan_data.get("tasks", []):
            plan.tasks.append(
                Task(
                    id=raw["id"],
                    agent=raw["agent"],
                    instruction=raw.get("instruction", ""),
                    depends_on=raw.get("depends_on", []),
                    status=raw.get("status", "pending"),
                    key_findings=raw.get("key_findings", []),
                    result_path=raw.get("result_path"),
                    error=raw.get("error"),
                    delegation_count=raw.get("delegation_count", 0),
                )
            )
        return plan
    return TaskPlan()


def _scope_file_path() -> Any:
    """Get the scope.json path."""
    from pathlib import Path  # noqa: PLC0415

    run_state = Path(".swarmkit") / "run-state" / "current"
    run_state.mkdir(parents=True, exist_ok=True)
    return run_state / "scope.json"


def _write_scope(args: dict[str, Any], agent_id: str) -> None:
    """Write scope.json to run-state directory (create-scope)."""
    scope_data = {
        "source": args.get("source", ""),
        "requirements": args.get("requirements", []),
        "constraints": args.get("constraints", []),
        "authoritative_sources": args.get("authoritative_sources", []),
        "excluded": args.get("excluded", []),
        "decisions": args.get("decisions", []),
        "related": args.get("related", []),
    }

    _scope_file_path().write_text(json.dumps(scope_data, indent=2), encoding="utf-8")
    _progress(
        f"[{agent_id}] scope created: {len(scope_data['requirements'])} requirements, "
        f"{len(scope_data['constraints'])} constraints"
    )


def _enforce_two_phase(
    raw_tasks: list[dict[str, Any]],
    planning_config: Any,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Enforce two-phase planning when scope_required or two_phase is set.

    When scope doesn't exist yet, strip non-research tasks from the plan.
    Research tasks = tasks with no dependencies and agent != 'self' and
    agent != 'document-writer'. This forces the model to do research
    first, create scope, then add Phase 2 tasks via update-task-plan.

    This is a compiler enforcement — the model can't bypass it regardless
    of what the prompt says.
    """
    from ._state import PlanningConfig  # noqa: PLC0415

    config = planning_config or PlanningConfig()
    if not config.scope_required and not config.two_phase:
        return raw_tasks

    scope_path = _scope_file_path()
    if scope_path.exists():
        return raw_tasks

    research_tasks = []
    stripped = []
    for task in raw_tasks:
        agent = task.get("agent", "")
        deps = task.get("depends_on", [])
        if agent in ("self", "document-writer") or deps:
            stripped.append(task.get("id", "unknown"))
        else:
            research_tasks.append(task)

    if stripped:
        _progress(
            f"  [{agent_id}] scope required — stripped {len(stripped)} non-research "
            f"tasks from initial plan: {', '.join(stripped)}. "
            f"Create scope first, then add Phase 2 tasks via update-task-plan."
        )

    return research_tasks


def _update_scope(args: dict[str, Any], agent_id: str) -> None:
    """Update scope.json with additive changes (update-scope)."""
    path = _scope_file_path()
    if not path.exists():
        _progress(f"[{agent_id}] update-scope called but no scope exists")
        return

    scope_data = json.loads(path.read_text(encoding="utf-8"))
    for field, key in [
        ("add_requirements", "requirements"),
        ("add_constraints", "constraints"),
        ("add_authoritative_sources", "authoritative_sources"),
        ("add_excluded", "excluded"),
        ("add_decisions", "decisions"),
        ("add_related", "related"),
    ]:
        items = args.get(field, [])
        if items:
            scope_data[key] = scope_data.get(key, []) + items

    path.write_text(json.dumps(scope_data, indent=2), encoding="utf-8")
    _progress(
        f"[{agent_id}] scope updated: {len(scope_data.get('requirements', []))} requirements, "
        f"{len(scope_data.get('constraints', []))} constraints"
    )
