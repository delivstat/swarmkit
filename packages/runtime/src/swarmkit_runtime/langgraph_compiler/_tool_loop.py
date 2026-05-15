"""Multi-turn tool execution loop.

Runs the model → tool-call → result cycle until the model produces
a final text response or the turn limit is hit.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.model_providers import CompletionResponse, ContentBlock, Message
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.resolver import ResolvedAgent

from ._helpers import ToolCallResult, _extract_text, _progress, _truncate_result
from ._prompts import _build_completion_request, _find_tasks_json, _looks_incomplete

_MAX_TOOL_CALLS_PER_TURN = int(os.environ.get("SWARMKIT_MAX_TOOLS", "10"))


def _handle_read_task_result(block: Any, agent_id: str) -> str:  # noqa: PLR0911
    """Handle read-task-result as a tool call, not a state change."""
    import json as _json  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    args = block.tool_input
    if isinstance(args, str):
        try:
            args = _json.loads(args)
        except (ValueError, TypeError):
            args = {}
    if not isinstance(args, dict):
        args = {}

    task_id = args.get("task_id", "")
    if not task_id:
        return "Error: task_id required"

    tasks_path = _find_tasks_json()
    if not tasks_path:
        return f"No task plan found for task '{task_id}'"

    from swarmkit_runtime.langgraph_compiler._task_plan import TaskPlan  # noqa: PLC0415

    plan = TaskPlan.load(tasks_path)
    task = plan.get_task(task_id)
    if not task:
        return f"Task '{task_id}' not found in plan"
    if task.status != "completed":
        return f"Task '{task_id}' is {task.status}, not completed"
    if not task.result_path:
        return f"Task '{task_id}' has no result file"

    result_path = _Path(task.result_path)
    if not result_path.exists():
        return f"Result file not found: {task.result_path}"

    content = result_path.read_text(encoding="utf-8")
    _progress(f"  [{agent_id}] read task result '{task_id}' ({len(content)} chars)")
    if len(content) > 50_000:
        content = content[:50_000] + "\n\n(truncated)"
    return content


async def _handle_skill_tool_calls(  # noqa: PLR0912
    response: CompletionResponse,
    agent: ResolvedAgent,
    model_provider: ModelProviderProtocol,
    model_name: str,
    mcp_manager: Any = None,
    governance: GovernanceProvider | None = None,
) -> list[ToolCallResult] | None:
    """Execute all skill tool calls in the response (up to max per turn).

    Returns structured results so the caller can build tool_result messages
    for a synthesis follow-up call.
    """
    import json  # noqa: PLC0415

    from swarmkit_runtime.langgraph_compiler._skill_executor import execute_skill  # noqa: PLC0415

    skill_map = {s.id: s for s in agent.skills}
    results: list[ToolCallResult] = []
    _verbose = os.environ.get("SWARMKIT_VERBOSE", "")

    for block in response.content:
        if len(results) >= _MAX_TOOL_CALLS_PER_TURN:
            if _verbose:
                print(
                    f"  [max tool calls reached: {_MAX_TOOL_CALLS_PER_TURN}]",
                    file=sys.stderr,
                )
            break
        if block.type != "tool_use" or not block.tool_name:
            continue
        if block.tool_name.startswith("delegate_to_"):
            continue
        if block.tool_name in (
            "create-task-plan",
            "update-task-plan",
        ):
            continue
        if block.tool_name == "read-task-result":
            result_text = _handle_read_task_result(block, agent.id)
            results.append(
                ToolCallResult(
                    tool_use_id=block.tool_use_id or f"call_{len(results)}",
                    tool_name=block.tool_name,
                    result=result_text,
                    image_blocks=[],
                )
            )
            continue
        skill = skill_map.get(block.tool_name)
        if skill is None:
            continue
        input_text = ""
        if isinstance(block.tool_input, dict):
            input_text = json.dumps(block.tool_input)
        elif isinstance(block.tool_input, str):
            input_text = block.tool_input
        _mcp_args_preview = ""
        if isinstance(block.tool_input, dict):
            _mcp_args_preview = " " + json.dumps(block.tool_input)[:100]
        _progress(f"  [{agent.id}] calling {block.tool_name}{_mcp_args_preview}")
        if _verbose:
            print(f"  executing: {block.tool_name}", file=sys.stderr)
        raw_result = await execute_skill(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=os.environ.get("SWARMKIT_MODEL") or model_name,
            mcp_manager=mcp_manager,
            governance=governance,
            agent_id=agent.id,
        )
        if isinstance(raw_result, tuple):
            text_result, images = raw_result
        else:
            text_result, images = raw_result, []
        results.append(
            ToolCallResult(
                tool_use_id=block.tool_use_id or f"call_{len(results)}",
                tool_name=block.tool_name,
                result=text_result or "(no result)",
                image_blocks=images,
            )
        )

    return results if results else None


async def _run_tool_loop(  # noqa: PLR0912, PLR0915
    response: CompletionResponse,
    agent: ResolvedAgent,
    messages: list[Message],
    tools: list[Any],
    model_name: str,
    system_prompt: str | None,
    model_provider: ModelProviderProtocol,
    mcp_manager: Any,
    governance: GovernanceProvider,
    tool_results: list[ToolCallResult],
    verbose: str,
) -> str:
    """Run the multi-turn tool loop until final text or turn limit.

    Keeps calling the model with tool results until it produces a final
    text response (no more tool calls) or we hit the turn limit. Includes
    nudging for incomplete responses.
    """
    _max_tool_turns = int(os.environ.get("SWARMKIT_MAX_TOOL_TURNS", "50"))
    loop_messages = list(messages)
    current_response = response
    current_results = tool_results

    for _turn in range(_max_tool_turns):
        assistant_blocks = list(current_response.content)
        tool_result_blocks: list[ContentBlock] = []
        for tr in current_results:
            tool_result_blocks.append(
                ContentBlock(
                    type="tool_result",
                    tool_use_id=tr.tool_use_id,
                    tool_result=_truncate_result(tr.result),
                )
            )
            tool_result_blocks.extend(tr.image_blocks)
        loop_messages.append(
            Message(role="assistant", content=assistant_blocks),
        )
        loop_messages.append(
            Message(role="user", content=tool_result_blocks),
        )
        follow_up = _build_completion_request(
            model_name, loop_messages, system_prompt, tools, agent
        )
        _result_summaries = []
        for tr in current_results:
            _size = len(tr.result)
            if _size > 1024:
                _result_summaries.append(f"{tr.tool_name} ({_size // 1024}KB)")
            else:
                _result_summaries.append(f"{tr.tool_name} ({_size}B)")
        _progress(
            f"  [{agent.id}] got results: {', '.join(_result_summaries)} "
            f"| waiting for model... (turn {_turn + 1})"
        )
        if verbose:
            print(
                f"  [tool loop turn {_turn + 1}: {len(current_results)} tool results]",
                file=sys.stderr,
            )
        current_response = await model_provider.complete(follow_up)

        next_results = await _handle_skill_tool_calls(
            current_response,
            agent,
            model_provider,
            model_name,
            mcp_manager,
            governance,
        )
        if next_results is None:
            # Model returned text without tool calls. Check if it looks
            # incomplete (planning language) and nudge it to continue.
            text = _extract_text(current_response)
            if _turn < _max_tool_turns - 1 and _looks_incomplete(text):
                if verbose:
                    print(
                        "  [nudge: stripping planning text, prompting to use tools]",
                        file=sys.stderr,
                    )
                _progress(f"  [{agent.id}] stripped planning text: {text[:80]}")
                loop_messages.append(
                    Message(
                        role="user",
                        content=(
                            "You described what you plan to do but "
                            "didn't do it. Call the tools now. "
                            "Do NOT describe your next step — execute it."
                        ),
                    ),
                )
                nudge_req = _build_completion_request(
                    model_name, loop_messages, system_prompt, tools, agent
                )
                current_response = await model_provider.complete(nudge_req)
                next_results = await _handle_skill_tool_calls(
                    current_response,
                    agent,
                    model_provider,
                    model_name,
                    mcp_manager,
                    governance,
                )
                if next_results is None:
                    break
                current_results = next_results
                continue
            break
        current_results = next_results

    text = _extract_text(current_response)
    if text:
        return text

    _progress(f"  [{agent.id}] tool limit reached — synthesizing final answer...")
    if verbose:
        print("  [tool limit reached — forcing synthesis]", file=sys.stderr)

    if current_results:
        last_result_blocks: list[ContentBlock] = []
        for tr in current_results:
            last_result_blocks.append(
                ContentBlock(
                    type="tool_result",
                    tool_use_id=tr.tool_use_id,
                    tool_result=_truncate_result(tr.result),
                )
            )
        loop_messages.append(
            Message(role="assistant", content=list(current_response.content)),
        )
        loop_messages.append(
            Message(role="user", content=last_result_blocks),
        )

    loop_messages.append(
        Message(
            role="user",
            content=(
                "STOP. Do NOT call any more tools. You have gathered enough information. "
                "Write your complete, detailed analysis NOW based on everything you found. "
                "This is your final response — synthesize all findings into a coherent answer. "
                "Start your response with: '## Analysis (tool limit reached)' so the "
                "coordinator knows this is a complete best-effort answer and should NOT "
                "re-delegate this task."
            ),
        ),
    )
    synthesis_req = _build_completion_request(model_name, loop_messages, system_prompt, [], agent)
    synthesis_response = await model_provider.complete(synthesis_req)
    return _extract_text(synthesis_response) or "(analysis incomplete — tool limit reached)"
