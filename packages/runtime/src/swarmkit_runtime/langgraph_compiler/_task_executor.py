"""Task execution engine for structured delegation.

Executes runnable tasks from a TaskPlan — delegates to child agents
for child tasks, runs tools inline for self-tasks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from swarmkit_runtime.langgraph_compiler._helpers import _progress
from swarmkit_runtime.langgraph_compiler._run_context import run_state_dir
from swarmkit_runtime.langgraph_compiler._sentinels import AgentStatus
from swarmkit_runtime.langgraph_compiler._state import DEFAULT_SYNTHESIZER_ROLE
from swarmkit_runtime.langgraph_compiler._task_plan import Task, TaskPlan
from swarmkit_runtime.model_providers._registry import (
    ModelProviderProtocol,
    ProviderRegistry,
)
from swarmkit_runtime.resolver import ResolvedAgent

from ._state import SwarmState


def get_plan_from_state(state: SwarmState) -> TaskPlan | None:
    """Reconstruct a TaskPlan from SwarmState if one exists."""
    plan_data = state.get("task_plan", {})
    if not plan_data or not plan_data.get("tasks"):
        return None
    return TaskPlan.from_dict(plan_data)


async def execute_task_batch(  # noqa: PLR0915
    plan: TaskPlan,
    agent: ResolvedAgent,
    agent_id: str,
    model_provider: ModelProviderProtocol,
    governance: Any,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any,
    provider_registry: ProviderRegistry | None,
    workspace_root: Path | None = None,
    decision_skill_bindings: list[Any] | None = None,
    synthesis_config: Any = None,
    original_input: str = "",
    synthesizer_role: str = DEFAULT_SYNTHESIZER_ROLE,
) -> dict[str, Any]:
    """Execute the next batch of runnable tasks from the plan.

    Returns a state dict with updated task_plan, agent_results,
    and messages from completed tasks.
    """
    runnable = plan.get_runnable_tasks()
    if not runnable:
        if plan.all_done():
            result = await _maybe_synthesize(
                synthesis_config,
                workspace_root,
                original_input,
                provider_registry,
                plan,
                agent_id,
                synthesizer_role,
            )
            if result is not None:
                return result
            return _plan_complete_state(plan, agent_id)
        return _plan_blocked_state(plan, agent_id)

    child_map = {c.id: c for c in agent.children}
    names = [t.id for t in runnable]
    _progress(f"[{agent_id}] executing task batch: {', '.join(names)}")

    async def _run_one(task: Task) -> tuple[str, str]:
        plan.mark_started(task.id)
        _persist_plan(plan, workspace_root, agent_id)
        try:
            if task.agent == "self":
                result = await _execute_self_task(
                    task,
                    agent,
                    agent_id,
                    model_provider,
                    mcp_manager,
                    governance,
                )
            else:
                result = await _execute_child_task(
                    task,
                    child_map.get(task.agent),
                    agent_id,
                    model_provider,
                    governance,
                    all_agents,
                    mcp_manager,
                    provider_registry,
                )
            result_path = _save_result(
                task.id,
                result,
                workspace_root,
                agent_id,
            )
            import os as _os  # noqa: PLC0415

            _coord_model = _os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get(
                "name", "mock"
            )
            findings = await _summarize_result(
                task.id,
                result,
                model_provider,
                coordinator_model=_coord_model,
            )
            plan.mark_completed(
                task.id,
                key_findings=findings,
                result_path=result_path,
            )
            _progress(f"  task '{task.id}' completed ({len(findings)} findings)")
        except Exception as exc:
            result = f"Error: {exc}"
            plan.mark_failed(task.id, str(exc))
            _progress(f"  task '{task.id}' failed: {exc}")

        _persist_plan(plan, workspace_root, agent_id)
        return (task.id, result)

    tasks_coros = [_run_one(t) for t in runnable]
    results = await asyncio.gather(*tasks_coros)

    _bindings = decision_skill_bindings or []

    merged_results: dict[str, str] = {}
    messages = []
    for tid, raw_text in results:
        checked_text = raw_text
        if _bindings:
            from swarmkit_runtime.langgraph_compiler._decision_gate import (  # noqa: PLC0415
                evaluate_post_output,
            )

            task_obj = plan.get_task(tid)
            _task_agent = task_obj.agent if task_obj else agent_id
            _task_instruction = task_obj.instruction if task_obj else ""

            retry_fn = _make_retry_fn(_task_instruction, raw_text, model_provider, agent)
            checked_text, _ = await evaluate_post_output(
                agent_id=_task_agent,
                output=raw_text,
                bindings=_bindings,
                governance=governance,
                retry_fn=retry_fn,
            )
        merged_results[tid] = checked_text
        messages.append(AIMessage(content=checked_text, name=tid))

    more_runnable = plan.get_runnable_tasks()
    if more_runnable or not plan.all_done():
        if _bindings:
            from swarmkit_runtime.langgraph_compiler._decision_gate import (  # noqa: PLC0415
                evaluate_checkpoint,
                format_gate_feedback,
            )

            checkpoint_results = await evaluate_checkpoint(
                agent_id=agent_id,
                task_results=merged_results,
                bindings=_bindings,
                governance=governance,
            )
            feedback = format_gate_feedback(checkpoint_results)
            if feedback:
                messages.append(AIMessage(content=feedback, name="governance"))

        return {
            "current_agent": agent_id,
            "task_plan": plan.to_dict(),
            "agent_results": {
                agent_id: AgentStatus.TASK_PLAN_EXECUTING,
                **merged_results,
            },
            "messages": messages,
        }

    if _bindings:
        from swarmkit_runtime.langgraph_compiler._decision_gate import (  # noqa: PLC0415
            evaluate_pre_synthesis,
            format_gate_feedback,
        )

        _all_results = {
            t.id: merged_results.get(t.id, "") for t in plan.tasks if t.id in merged_results
        }
        pre_synth_results = await evaluate_pre_synthesis(
            agent_id=agent_id,
            task_results=_all_results,
            original_input="",
            bindings=_bindings,
            governance=governance,
            workspace_root=workspace_root,
        )
        feedback = format_gate_feedback(pre_synth_results)
        if feedback:
            messages.append(AIMessage(content=feedback, name="governance"))

    result = await _maybe_synthesize(
        synthesis_config,
        workspace_root,
        original_input,
        provider_registry,
        plan,
        agent_id,
        synthesizer_role,
    )
    if result is not None:
        return result
    return _plan_complete_state(plan, agent_id, merged_results, messages)


async def _execute_child_task(
    task: Task,
    child: ResolvedAgent | None,
    parent_id: str,
    model_provider: ModelProviderProtocol,
    governance: Any,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any,
    provider_registry: ProviderRegistry | None,
) -> str:
    """Execute a task by running a child agent."""
    if child is None:
        return f"Error: agent '{task.agent}' not found"

    from swarmkit_runtime.langgraph_compiler._compiler import (  # noqa: PLC0415
        _build_agent_node,
        _resolve_agent_provider,
    )

    child_provider = _resolve_agent_provider(
        child,
        provider_registry,
        model_provider,
    )
    child_fn = _build_agent_node(
        child,
        child_provider,
        governance,
        all_agents,
        mcp_manager,
        provider_registry,
    )

    from swarmkit_runtime.langgraph_compiler._run_context import (  # noqa: PLC0415
        reset_parent_agent,
        set_parent_agent,
    )

    _parent_token = set_parent_agent(parent_id)

    child_state: SwarmState = {
        "input": task.instruction,
        "messages": [
            HumanMessage(
                content=task.instruction,
                name=parent_id,
            )
        ],
        "agent_results": {},
        "delegation_counts": {},
        "task_plan": {},
        "current_agent": child.id,
        "output": "",
    }

    result_state = await child_fn(child_state)
    reset_parent_agent(_parent_token)
    output = str(result_state.get("output", "(no response)"))

    from swarmkit_runtime.langgraph_compiler._prompts import (  # noqa: PLC0415
        _looks_incomplete,
    )

    if _looks_incomplete(output):
        _progress(
            f"  [{parent_id}] child '{task.agent}' returned incomplete, "
            f"extracting from tool results"
        )
        extracted = _extract_findings_from_messages(child_state.get("messages", []))
        if extracted:
            _progress(f"  [{parent_id}] extracted {len(extracted)} findings from tool results")
            output = extracted
        else:
            output = f"(agent returned planning text instead of findings: {output[:100]})"
    return output


def _extract_findings_from_messages(messages: list[Any]) -> str:  # noqa: PLR0912
    """Extract findings from tool result messages when synthesis fails.

    Scans messages for tool_result content blocks with provenance tags
    and builds a minimal structured output from the raw tool data.
    This salvages useful data from agents that hit the turn limit
    without producing a final synthesis.
    """
    import json as _json  # noqa: PLC0415

    from swarmkit_runtime.langgraph_compiler._output_schema import (  # noqa: PLC0415
        _SOURCE_TAG_RE,
    )

    findings: list[dict[str, str]] = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            for match in _SOURCE_TAG_RE.finditer(content):
                source = match.group(1).strip()
                text_before = content[: match.start()].strip()
                if text_before and len(text_before) > 20:
                    last_line = text_before.split("\n")[-1].strip()[:200]
                    if last_line:
                        findings.append({"fact": last_line, "source": source})
            continue
        if not isinstance(content, (list, tuple)):
            continue
        for block in content:
            text = getattr(block, "tool_result", None)
            if not isinstance(text, str) or len(text) < 30:
                continue
            for match in _SOURCE_TAG_RE.finditer(text):
                source = match.group(1).strip()
                text_before = text[: match.start()].strip()
                lines = [ln.strip() for ln in text_before.split("\n") if ln.strip()]
                for line in lines[-3:]:
                    if len(line) > 20:
                        findings.append({"fact": line[:200], "source": source})

    if not findings:
        return ""

    findings = findings[:20]
    result: dict[str, Any] = {
        "findings": findings,
        "not_found": ["synthesis failed — extracted from raw tool results"],
    }
    return _json.dumps(result)


async def _execute_self_task(
    task: Task,
    agent: ResolvedAgent,
    agent_id: str,
    model_provider: ModelProviderProtocol,
    mcp_manager: Any,
    governance: Any,
) -> str:
    """Execute a self-task — the coordinator does this work itself."""
    import os  # noqa: PLC0415

    from swarmkit_runtime.langgraph_compiler._prompts import (  # noqa: PLC0415
        _build_completion_request,
        _build_system_prompt,
        _build_tools,
    )
    from swarmkit_runtime.langgraph_compiler._tool_loop import (  # noqa: PLC0415
        _handle_skill_tool_calls,
        _run_tool_loop,
    )
    from swarmkit_runtime.model_providers import Message  # noqa: PLC0415

    tools = _build_tools(agent, mcp_manager=mcp_manager)
    # Only keep skill tools for self-tasks, not planning tools
    tools = [
        t
        for t in tools
        if t.name
        not in (
            "create-task-plan",
            "update-task-plan",
            "read-task-result",
            "create-scope",
            "update-scope",
            "read-scope",
        )
    ]

    model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")
    system_prompt = _build_system_prompt(agent, tools)
    messages = [
        Message(role="user", content=task.instruction),
    ]

    request = _build_completion_request(
        model_name,
        messages,
        system_prompt,
        tools,
        agent,
    )
    _progress(f"  [{agent_id}] self-task: {task.id}")
    response = await model_provider.complete(request)

    tool_results = await _handle_skill_tool_calls(
        response,
        agent,
        model_provider,
        model_name,
        mcp_manager,
        governance,
    )
    if tool_results is not None:
        verbose = os.environ.get("SWARMKIT_VERBOSE", "")
        loop_result = await _run_tool_loop(
            response,
            agent,
            messages,
            tools,
            model_name,
            system_prompt,
            model_provider,
            mcp_manager,
            governance,
            tool_results,
            verbose,
        )
        if isinstance(loop_result, dict):
            return "(state change during self-task)"
        return loop_result

    from swarmkit_runtime.langgraph_compiler._helpers import (  # noqa: PLC0415
        _extract_text,
    )

    return _extract_text(response) or "(no output)"


async def _summarize_result(
    task_id: str,
    result: str,
    model_provider: ModelProviderProtocol,
    coordinator_model: str = "",
) -> list[str]:
    """Generate 3-5 bullet key findings from a task result.

    If the result is valid structured JSON with a ``findings`` array,
    extract findings directly without an LLM call. Otherwise uses
    the coordinator's model for summarization.
    """
    import json as _json  # noqa: PLC0415

    try:
        parsed = _json.loads(result)
        if isinstance(parsed, dict) and "findings" in parsed:
            findings = parsed["findings"]
            if isinstance(findings, list):
                extracted: list[str] = []
                for f in findings:
                    if isinstance(f, dict) and "fact" in f:
                        fact = f["fact"]
                        source = f.get("source", "")
                        extracted.append(f"{fact} [{source}]" if source else fact)
                if extracted:
                    _progress(
                        f"  [{task_id}] structured output: "
                        f"{len(extracted)} findings (no summarizer needed)"
                    )
                    return extracted[:10]
                return []
    except (ValueError, TypeError):
        pass

    if len(result) < 500:
        return [result.strip()] if result.strip() else []

    if len(result) > 50000:
        result = result[:50000] + "\n\n(truncated)"

    from swarmkit_runtime.model_providers import (  # noqa: PLC0415
        CompletionRequest,
        Message,
    )

    model_name = coordinator_model or "mock"
    try:
        summary_request = CompletionRequest(
            model=model_name,
            messages=(
                Message(
                    role="user",
                    content=(
                        "Summarize the following research findings into "
                        "3-5 bullet points. Each bullet should be one "
                        "concise sentence capturing a key finding. "
                        "Focus on specific names, IDs, and data — not "
                        "generic descriptions.\n\n"
                        f"--- FINDINGS ---\n{result}\n--- END ---\n\n"
                        "Return ONLY the bullet points, one per line, "
                        "starting with '- '."
                    ),
                ),
            ),
            system=None,
            tools=None,
        )
        _progress(f"  [{task_id}] summarizing results...")
        summary_response = await model_provider.complete(summary_request)

        from swarmkit_runtime.langgraph_compiler._helpers import (  # noqa: PLC0415
            _extract_text,
        )

        raw = _extract_text(summary_response)
        if raw:
            findings = [
                line.lstrip("- ").strip()
                for line in raw.strip().split("\n")
                if line.strip() and line.strip().startswith("-")
            ]
            return findings[:5] if findings else [raw[:200]]
    except Exception:
        pass

    first_line = result.strip().split("\n")[0][:200]
    return [first_line] if first_line else []


def _save_result(
    task_id: str,
    result: str,
    workspace_root: Path | None,
    agent_id: str,
) -> str | None:
    """Save task result to disk. Returns the file path."""
    if not workspace_root:
        return None
    run_state = run_state_dir(workspace_root)
    path = run_state / f"{task_id}.md"
    path.write_text(result, encoding="utf-8")
    return str(path)


def _persist_plan(
    plan: TaskPlan,
    workspace_root: Path | None,
    agent_id: str,
) -> None:
    """Write tasks.json to disk."""
    if not workspace_root:
        return
    run_state = run_state_dir(workspace_root)
    plan.save(run_state)


def _make_retry_fn(
    task_instruction: str,
    original_output: str,
    model_provider: ModelProviderProtocol,
    agent: ResolvedAgent,
) -> Any:
    """Create a retry function for governance post_output retries.

    The retry function re-prompts the coordinator's model with:
    - Original task instruction (context)
    - Agent's previous output (what it produced)
    - Governance feedback (what to fix)

    The agent doesn't re-run tools — it revises using data it already has.
    """
    import os  # noqa: PLC0415

    from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

    model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")

    async def _retry(feedback: str) -> str:
        request = CompletionRequest(
            model=model_name,
            messages=(
                Message(
                    role="user",
                    content=(
                        f"You previously produced this output for the task:\n"
                        f"TASK: {task_instruction}\n\n"
                        f"YOUR PREVIOUS OUTPUT:\n{original_output}\n\n"
                        f"GOVERNANCE FEEDBACK:\n{feedback}"
                    ),
                ),
            ),
            system=(
                "You are revising your previous output based on governance "
                "feedback. Fix the specific issues flagged. Write the "
                "COMPLETE revised response."
            ),
            tools=None,
        )
        response = await model_provider.complete(request)
        from swarmkit_runtime.langgraph_compiler._helpers import (  # noqa: PLC0415
            _extract_text,
        )

        return _extract_text(response) or original_output

    return _retry


async def _maybe_synthesize(
    synthesis_config: Any,
    workspace_root: Path | None,
    original_input: str,
    provider_registry: ProviderRegistry | None,
    plan: TaskPlan,
    agent_id: str,
    synthesizer_role: str = DEFAULT_SYNTHESIZER_ROLE,
) -> dict[str, Any] | None:
    """Invoke the synthesizer if configured and scope exists.

    Returns a final state dict with the synthesized document as output,
    or None if synthesis is not configured or scope doesn't exist.
    """
    from ._state import SynthesisConfig  # noqa: PLC0415

    if not isinstance(synthesis_config, SynthesisConfig) or not synthesis_config.model:
        return None

    scope_path = run_state_dir() / "scope.json"
    if not scope_path.exists():
        return None

    from ._synthesizer import run_synthesis  # noqa: PLC0415

    output = await run_synthesis(
        config=synthesis_config,
        workspace_root=workspace_root,
        original_input=original_input,
        provider_registry=provider_registry,
        synthesizer_role=synthesizer_role,
    )

    return {
        "current_agent": agent_id,
        "task_plan": plan.to_dict(),
        "agent_results": {agent_id: output},
        "messages": [
            AIMessage(content=output, name=agent_id),
        ],
        "output": output,
    }


def _plan_complete_state(
    plan: TaskPlan,
    agent_id: str,
    merged_results: dict[str, str] | None = None,
    messages: list[Any] | None = None,
) -> dict[str, Any]:
    """Build state dict when all tasks are done."""
    status = plan.render_status()
    return {
        "current_agent": agent_id,
        "task_plan": plan.to_dict(),
        "agent_results": {
            agent_id: AgentStatus.TASK_PLAN_COMPLETE,
            **(merged_results or {}),
        },
        "messages": (messages or [])
        + [
            AIMessage(
                content=f"All tasks complete.\n\n{status}",
                name=agent_id,
            ),
        ],
    }


def _plan_blocked_state(
    plan: TaskPlan,
    agent_id: str,
) -> dict[str, Any]:
    """Build state for when no tasks are runnable but plan isn't done."""
    status = plan.render_status()
    return {
        "current_agent": agent_id,
        "task_plan": plan.to_dict(),
        "agent_results": {
            agent_id: AgentStatus.TASK_PLAN_BLOCKED,
        },
        "messages": [
            AIMessage(
                content=(
                    f"Task plan blocked — no runnable tasks.\n\n"
                    f"{status}\n\n"
                    f"Call update-task-plan to adjust dependencies "
                    f"or remove blocked tasks."
                ),
                name=agent_id,
            ),
        ],
    }
