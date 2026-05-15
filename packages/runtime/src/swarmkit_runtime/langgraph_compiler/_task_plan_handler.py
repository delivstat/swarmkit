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
) -> dict[str, Any] | None:
    """Handle create-task-plan, update-task-plan, read-task-result tool calls.

    Returns a state dict if a task plan tool was called, None otherwise.
    """
    from swarmkit_runtime.langgraph_compiler._task_plan import TaskPlan  # noqa: PLC0415

    _TASK_PLAN_TOOLS = {"create-task-plan", "update-task-plan", "read-task-result"}

    for block in response.content:
        if not hasattr(block, "tool_name") or not block.tool_name:
            continue
        if block.tool_name not in _TASK_PLAN_TOOLS:
            continue

        args = block.tool_input
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        if not isinstance(args, dict):
            args = {}

        existing_plan_data = (state or {}).get("task_plan", {})
        if existing_plan_data and existing_plan_data.get("tasks"):
            plan = TaskPlan(
                run_id=existing_plan_data.get("run_id", ""),
                topology=existing_plan_data.get("topology", ""),
                created_at=existing_plan_data.get("created_at", 0.0),
            )
            for raw in existing_plan_data.get("tasks", []):
                from swarmkit_runtime.langgraph_compiler._task_plan import Task  # noqa: PLC0415

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
        else:
            plan = TaskPlan()

        if block.tool_name == "create-task-plan":
            raw_tasks = args.get("tasks", [])
            if not raw_tasks:
                continue

            import time as _time  # noqa: PLC0415

            plan.run_id = (state or {}).get("input", "")[:50]
            plan.created_at = _time.time()
            errors = plan.add_tasks(raw_tasks)
            valid_agents = {c.id for c in agent.children}
            errors.extend(plan.validate_agents(valid_agents))
            fixes = plan.auto_fix_dependencies()
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
            upd_errors.extend(plan.validate_dependencies())

            _progress(f"[{agent_id}] updated task plan")
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
                        content=f"[{agent_id}] Task plan updated.",
                        name=agent_id,
                    ),
                ],
            }

        if block.tool_name == "read-task-result":
            pass

    return None
