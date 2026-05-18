"""Prompt and tool construction for compiled agent nodes.

Builds system prompts, message lists, tool specs, and completion
requests for each agent's model call.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from swarmkit_runtime.model_providers import CompletionRequest, Message, ToolSpec
from swarmkit_runtime.resolver import ResolvedAgent

from ._state import SwarmState


def _check_scope_exists() -> bool:
    """Check if scope.json exists in run state."""
    return Path(".swarmkit/run-state/current/scope.json").exists()


def _find_tasks_json() -> Path | None:
    """Find tasks.json on disk for resume scenarios."""
    candidates = [
        Path(".swarmkit/run-state/current/tasks.json"),
        Path(".swarmkit") / "run-state" / "current" / "tasks.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _build_completion_request(
    model_name: str,
    messages: list[Message] | tuple[Message, ...],
    system_prompt: str | None,
    tools: list[ToolSpec],
    agent: ResolvedAgent,
) -> CompletionRequest:
    """Build a CompletionRequest -- single source for the repeated construction."""
    from ._output_schema import get_effective_output_schema  # noqa: PLC0415

    effective_schema = get_effective_output_schema(agent)
    response_format = {"type": "json_object"} if effective_schema else None

    return CompletionRequest(
        model=model_name,
        messages=tuple(messages),
        system=system_prompt,
        tools=tuple(tools) if tools else None,
        temperature=(agent.model or {}).get("temperature"),
        response_format=response_format,
    )


def _get_completed_children(
    agent: ResolvedAgent,
    agent_results: dict[str, Any],
) -> set[str]:
    """Return child IDs that have completed with meaningful results.

    Excludes delegated markers, error results, and short incomplete
    responses so the root can re-delegate on the next turn.
    """
    completed = set()
    for c in agent.children:
        if c.id not in agent_results:
            continue
        result = agent_results[c.id]
        if not isinstance(result, str):
            continue
        if result.startswith("__delegated__:"):
            continue
        if result.startswith("DENIED:"):
            continue
        if result == "(no response)":
            continue
        if len(result) < 100 and _looks_incomplete(result):
            continue
        completed.add(c.id)
    return completed


_INCOMPLETE_MARKERS = [
    "let me",
    "i'll ",
    "i will ",
    "i need to",
    "next, i",
    "now i'll",
    "now let me",
    "i should",
    "to find out",
    "to examine",
    "to investigate",
    "to read",
    "to check",
    "let me now",
    "let me search",
    "let me look",
    "i noticed",
    "i want to",
    "i'm going to",
    "let's look",
    "let's check",
    "let's search",
]


def _looks_incomplete(text: str) -> bool:
    """Check if a response contains planning language without actual results."""
    lower = text.lower().strip()
    if not lower or lower == "(no response)":
        return True
    import json as _json  # noqa: PLC0415

    try:
        _json.loads(text)
        return False
    except (ValueError, TypeError):
        pass
    return len(lower) < 500 and any(m in lower for m in _INCOMPLETE_MARKERS)


def _build_system_prompt(agent: ResolvedAgent, tools: list[ToolSpec]) -> str | None:
    """Build the system prompt, injecting tool-use instructions when needed.

    The topology author's system prompt is preserved. The compiler appends
    a brief instruction block listing available tools so the model knows
    to call them instead of describing what it would do in text.
    """
    base = (agent.prompt or {}).get("system", "") or ""
    from ._output_schema import get_effective_output_schema  # noqa: PLC0415

    effective_schema = get_effective_output_schema(agent)
    if not tools and not effective_schema:
        return base or None

    _PLANNING_TOOL_NAMES = {
        "create-task-plan",
        "update-task-plan",
        "read-task-result",
        "create-scope",
        "update-scope",
        "read-scope",
    }
    planning_tools = [t for t in tools if t.name in _PLANNING_TOOL_NAMES]
    delegation_tools = [t for t in tools if t.name.startswith("delegate_to_")]
    skill_tools = [
        t for t in tools if not t.name.startswith("delegate_to_") and t not in planning_tools
    ]

    parts = [base.rstrip()] if base else []
    if tools:
        parts.append(
            "\nYou have the following tools available. "
            "Use them to act - do not describe what you would do."
        )

    if planning_tools:
        names = ", ".join(f"`{t.name}`" for t in planning_tools)
        parts.append(
            "\nTASK PLANNING: Call `create-task-plan` to define tasks "
            "for your workers and yourself. The compiler will execute "
            "tasks and report back with summaries. Call `update-task-plan` "
            "to adjust the plan at checkpoints. Use `read-task-result` "
            "to read full results from completed tasks.\n\n"
            "SCOPE: Call `create-scope` after reading source material "
            "to define requirements, constraints, and exclusions. Call "
            "`update-scope` when research reveals new constraints. Call "
            "`read-scope` to review the current scope before synthesis. "
            "The governance layer validates output against the scope."
        )
    elif delegation_tools:
        names = ", ".join(f"`{t.name}`" for t in delegation_tools)
        parts.append(
            f"DELEGATION (MANDATORY for root agents): You MUST delegate by calling "
            f"one of: {names}. Do NOT answer questions directly — always delegate."
        )

    if skill_tools:
        names = ", ".join(f"`{t.name}`" for t in skill_tools)
        parts.append(f"Skills available: {names}")

    if effective_schema:
        import json as _json  # noqa: PLC0415

        schema_json = _json.dumps(effective_schema, indent=2)
        parts.append(
            "\n\nSTRUCTURED OUTPUT: You MUST respond with valid JSON matching "
            "this schema:\n"
            f"```json\n{schema_json}\n```\n"
            "Return ONLY the JSON object. No markdown, no explanation, no "
            "preamble. Every finding must have a 'fact' and 'source' field. "
            'If you found nothing relevant, return {"findings": [], '
            '"not_found": ["<what you searched for>"]}.'
        )

    return "\n".join(parts)


def _build_prompt_messages(  # noqa: PLR0912, PLR0915
    agent: ResolvedAgent,
    state: SwarmState,
    planning_config: Any = None,
) -> list[Message]:
    """Build the message list for an agent's model call.

    For v2 (task plan): builds context from task plan status.
    For v1 (delegation): builds from child agent_results.
    """
    from ._state import PlanningConfig  # noqa: PLC0415

    _planning = planning_config or PlanningConfig()
    messages: list[Message] = []
    agent_results = state.get("agent_results", {})

    # ---- v2: task plan context ----------------------------------------
    agent_result = agent_results.get(agent.id, "")
    _is_task_plan_state = isinstance(agent_result, str) and agent_result.startswith("__task_plan_")
    if _is_task_plan_state:
        from swarmkit_runtime.langgraph_compiler._task_executor import (  # noqa: PLC0415
            get_plan_from_state,
        )

        plan = get_plan_from_state(state)
        if plan is None:
            from swarmkit_runtime.langgraph_compiler._task_plan import (  # noqa: PLC0415
                TaskPlan,
            )

            _disk_path = _find_tasks_json()
            if _disk_path:
                plan = TaskPlan.load(_disk_path)

        if plan:
            status = plan.render_status()
            _has_self_task = any(t.agent == "self" for t in plan.tasks)
            _is_phase1_done = plan.all_done() and not _has_self_task
            _needs_scope = _planning.two_phase or _is_phase1_done
            _scope_exists = _check_scope_exists()

            if plan.all_done() and _needs_scope and not _scope_exists:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"Original request: {state.get('input', '')}\n\n"
                            f"{status}\n\n"
                            f"Research phase complete. You MUST now:\n"
                            f"1. Call read-task-result to read the full "
                            f"findings\n"
                            f"2. Call create-scope to define requirements, "
                            f"constraints, and exclusions from what you "
                            f"learned\n"
                            f"3. Call update-task-plan to add targeted "
                            f"Phase 2 tasks OR write the final response "
                            f"if no further research is needed."
                        ),
                    )
                )
            elif plan.all_done() and _planning.scope_required and not _scope_exists:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"Original request: {state.get('input', '')}\n\n"
                            f"{status}\n\n"
                            f"All tasks are complete but scope is required. "
                            f"Call create-scope to define requirements and "
                            f"constraints before synthesizing. Then write "
                            f"the final response."
                        ),
                    )
                )
            elif plan.all_done():
                _scope_hint = ""
                if _scope_exists:
                    _scope_hint = (
                        " Call read-scope to review the scope contract "
                        "and ensure your response satisfies all requirements."
                    )
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"Original request: {state.get('input', '')}\n\n"
                            f"{status}\n\n"
                            f"All tasks are complete. Synthesize the "
                            f"findings into a comprehensive final answer. "
                            f"Use read-task-result to access full details "
                            f"from any task if the summaries aren't "
                            f"enough.{_scope_hint}\n\nDo NOT create a new "
                            f"task plan. Write the final response now."
                        ),
                    )
                )
            else:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"Original request: {state.get('input', '')}\n\n"
                            f"{status}\n\n"
                            f"Review the results above. You can:\n"
                            f"- Call update-task-plan to add new tasks, "
                            f"remove pending tasks, or modify instructions\n"
                            f"- Call read-task-result to see full details "
                            f"from a completed task\n"
                            f"- Wait for pending tasks to complete "
                            f"(they will run automatically)\n\n"
                            f"If no changes needed, the pending tasks "
                            f"will execute next."
                        ),
                    )
                )
            return messages

    child_results = {
        cid: agent_results[cid]
        for cid in [c.id for c in agent.children]
        if cid in agent_results
        and isinstance(agent_results[cid], str)
        and not agent_results[cid].startswith("__delegated__:")
    }

    all_children_ids = {c.id for c in agent.children}
    all_done = child_results and set(child_results.keys()) == all_children_ids

    if all_done:
        results_text = "\n\n".join(f"[{cid}]:\n{result}" for cid, result in child_results.items())
        messages.append(
            Message(
                role="user",
                content=(
                    f"Original request: {state.get('input', '')}\n\n"
                    f"Your workers have produced the following results:\n\n{results_text}\n\n"
                    f"Present the workers' output directly to the user as the final response. "
                    f"Do not add commentary about the workers or the delegation process — "
                    f"just deliver the result as if you produced it yourself. "
                    f"Do NOT re-delegate to the same worker — their response is final."
                ),
            )
        )
    elif child_results:
        results_text = "\n\n".join(f"[{cid}]:\n{result}" for cid, result in child_results.items())
        done_names = ", ".join(child_results.keys())
        remaining = all_children_ids - set(child_results.keys())
        remaining_names = ", ".join(remaining) if remaining else "none"
        messages.append(
            Message(
                role="user",
                content=(
                    f"Original request: {state.get('input', '')}\n\n"
                    f"Workers already completed ({done_names}):\n\n{results_text}\n\n"
                    f"Workers not yet called: {remaining_names}\n\n"
                    f"Continue with your workflow. Delegate to workers that "
                    f"have NOT been called yet. Do NOT re-delegate to "
                    f"{done_names} with the same task — only call them again "
                    f"if you have a NEW, DIFFERENT question based on what "
                    f"you learned from other workers."
                ),
            )
        )
    else:
        # Build compacted conversation context. Keep last N turns full,
        # summarise older turns to one line each. Only Q+A pairs — strip
        # internal delegations, tool calls, and raw tool outputs.
        _keep_recent = int(os.environ.get("SWARMKIT_HISTORY_TURNS", "3"))
        turns: list[tuple[str, str]] = []
        current_q = ""
        for msg in state.get("messages", []):
            if isinstance(msg, HumanMessage):
                current_q = str(msg.content)
            elif isinstance(msg, AIMessage) and msg.content:
                content = str(msg.content)
                if not content.startswith("__delegated__:") and current_q:
                    turns.append((current_q, content))
                    current_q = ""

        if turns:
            older = turns[:-_keep_recent] if len(turns) > _keep_recent else []
            recent = turns[-_keep_recent:]

            if older:
                summary_lines = ["Previous conversation context:"]
                for q, a in older:
                    q_short = q[:80] + "..." if len(q) > 80 else q
                    a_short = a[:120] + "..." if len(a) > 120 else a
                    summary_lines.append(f"- Q: {q_short} → A: {a_short}")
                messages.append(
                    Message(role="user", content="\n".join(summary_lines)),
                )
                messages.append(
                    Message(
                        role="assistant",
                        content="Understood, I have the context from previous turns.",
                    ),
                )

            for q, a in recent:
                messages.append(Message(role="user", content=q))
                messages.append(Message(role="assistant", content=a))
        else:
            task = state.get("input", "")
            last_human = None
            for msg in reversed(state.get("messages", [])):
                if isinstance(msg, HumanMessage):
                    last_human = msg.content
                    break
            messages.append(Message(role="user", content=str(last_human or task)))

    return messages


def _build_tools(agent: ResolvedAgent, mcp_manager: Any = None) -> list[ToolSpec]:
    """Map an agent's executable skills + children to ToolSpec objects.

    ``llm_prompt`` and ``mcp_tool`` skills are included. ``composed``
    skills are excluded (panel aggregation is invoked differently).

    For ``mcp_tool`` skills the ``inputSchema`` published by the MCP
    server is forwarded as the tool's ``input_schema`` so the LLM sees
    the correct parameter names rather than inventing its own.
    """
    from ._dag import _has_dag_deps  # noqa: PLC0415

    tools: list[ToolSpec] = []

    _executable_types = {"llm_prompt", "mcp_tool"}
    for skill in agent.skills:
        impl = skill.raw.implementation
        impl_type = impl.get("type") if isinstance(impl, dict) else getattr(impl, "type", None)
        if impl_type in _executable_types:
            desc = getattr(skill, "description", "") or skill.id
            input_schema: dict[str, Any] = {}
            if impl_type == "mcp_tool" and mcp_manager is not None:
                server_id = (
                    impl.get("server") if isinstance(impl, dict) else getattr(impl, "server", "")
                )
                tool_name = (
                    impl.get("tool") if isinstance(impl, dict) else getattr(impl, "tool", "")
                )
                input_schema = mcp_manager.get_tool_input_schema(server_id, tool_name)
            tools.append(ToolSpec(name=skill.id, description=desc, input_schema=input_schema))

    _use_task_plan = len(agent.children) >= 2 and not _has_dag_deps(agent)
    if _use_task_plan:
        from swarmkit_runtime.langgraph_compiler._task_plan import (  # noqa: PLC0415
            build_create_scope_tool,
            build_create_task_plan_tool,
            build_read_scope_tool,
            build_read_task_result_tool,
            build_update_scope_tool,
            build_update_task_plan_tool,
        )

        tools.append(build_create_task_plan_tool(agent))
        tools.append(build_update_task_plan_tool())
        tools.append(build_read_task_result_tool())
        tools.append(build_create_scope_tool())
        tools.append(build_update_scope_tool())
        tools.append(build_read_scope_tool())
    else:
        for child in agent.children:
            tools.append(
                ToolSpec(
                    name=f"delegate_to_{child.id}",
                    description=f"Delegate a task to {child.id} (role={child.role})",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "The task to delegate",
                            }
                        },
                        "required": ["task"],
                    },
                )
            )

    return tools
