"""Multi-turn tool execution loop.

Runs the model → tool-call → result cycle until the model produces
a final text response or the turn limit is hit.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from swarmkit_runtime.compression import maybe_compress_tool_result
from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.model_providers import CompletionResponse, ContentBlock, Message
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.resolver import ResolvedAgent

from ._helpers import ToolCallResult, _extract_text, _progress, _truncate_result
from ._prompts import _build_completion_request, _find_tasks_json, _looks_incomplete


def _record_tool_loop_tokens(agent_id: str, model: str, response: CompletionResponse) -> None:
    """Record tokens from a tool-loop LLM call into the active trace."""
    from swarmkit_runtime.langgraph_compiler._compiler import (  # noqa: PLC0415
        _active_trace,
    )

    if _active_trace is not None:
        _active_trace.record_llm_call(
            agent_id=agent_id,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _record_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    result_length: int,
    duration_ms: int,
    error: str | None = None,
) -> None:
    """Record a tool/MCP call into the active trace's current step."""
    from swarmkit_runtime.langgraph_compiler._compiler import (  # noqa: PLC0415
        _active_trace,
    )
    from swarmkit_runtime.trace import ToolCall  # noqa: PLC0415

    if _active_trace is None:
        return
    if not _active_trace.agent_steps:
        return
    _active_trace.agent_steps[-1].tool_calls.append(
        ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            result_length=result_length,
            duration_ms=duration_ms,
            error=error,
        )
    )


_MAX_TOOL_CALLS_PER_TURN = int(os.environ.get("SWARMKIT_MAX_TOOLS", "10"))


def _check_for_state_change(
    response: CompletionResponse,
    agent: ResolvedAgent,
    agent_id: str,
    state: Any,
) -> dict[str, Any] | None:
    """Check if model response contains state-changing tools (update-task-plan).

    If found, processes all tools via _handle_task_plan_tools and returns
    the state dict. This causes the tool loop to terminate and return
    the state change to the compiler.
    """
    has_state_tool = any(
        hasattr(b, "tool_name") and b.tool_name in ("create-task-plan", "update-task-plan")
        for b in response.content
    )
    if not has_state_tool:
        return None

    from ._task_plan_handler import _handle_task_plan_tools  # noqa: PLC0415

    disk_state = _load_state_from_disk()
    return _handle_task_plan_tools(response, agent, agent_id, disk_state)


def _load_state_from_disk() -> dict[str, Any]:
    """Load task plan state from disk for tool-loop state changes."""

    tasks_path = _find_tasks_json()
    if not tasks_path:
        return {}

    from swarmkit_runtime.langgraph_compiler._task_plan import TaskPlan  # noqa: PLC0415

    plan = TaskPlan.load(tasks_path)
    return {"task_plan": plan.to_dict()}


def _scope_path() -> Any:
    from pathlib import Path as _Path  # noqa: PLC0415

    run_state = _Path(".swarmkit") / "run-state" / "current"
    run_state.mkdir(parents=True, exist_ok=True)
    return run_state / "scope.json"


def _parse_tool_args(block: Any) -> dict[str, Any]:
    import json as _json  # noqa: PLC0415

    args = block.tool_input
    if isinstance(args, str):
        try:
            args = _json.loads(args)
        except (ValueError, TypeError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    return args


def _handle_create_scope(block: Any, agent_id: str) -> str:
    """Create the scope contract — called once after reading source material."""
    import json as _json  # noqa: PLC0415

    args = _parse_tool_args(block)
    scope_data = {
        "source": args.get("source", ""),
        "requirements": args.get("requirements", []),
        "constraints": args.get("constraints", []),
        "authoritative_sources": args.get("authoritative_sources", []),
        "excluded": args.get("excluded", []),
        "decisions": args.get("decisions", []),
        "related": args.get("related", []),
        "solution_approach": args.get("solution_approach", []),
        "open_questions": args.get("open_questions", []),
    }

    _scope_path().write_text(_json.dumps(scope_data, indent=2), encoding="utf-8")

    reqs = len(scope_data["requirements"])
    constraints = len(scope_data["constraints"])
    _progress(f"[{agent_id}] scope created: {reqs} requirements, {constraints} constraints")
    return (
        f"Scope created. {reqs} requirements, {constraints} constraints. "
        f"Call update-scope if research reveals new constraints."
    )


def _handle_update_scope(block: Any, agent_id: str) -> str:
    """Update the scope with new findings — additive only."""
    import json as _json  # noqa: PLC0415

    path = _scope_path()
    if not path.exists():
        return "Error: no scope exists. Call create-scope first."

    scope_data = _json.loads(path.read_text(encoding="utf-8"))
    args = _parse_tool_args(block)

    added: list[str] = []
    for field, key in [
        ("add_requirements", "requirements"),
        ("add_constraints", "constraints"),
        ("add_authoritative_sources", "authoritative_sources"),
        ("add_excluded", "excluded"),
        ("add_decisions", "decisions"),
        ("add_related", "related"),
        ("add_solution_approach", "solution_approach"),
        ("add_open_questions", "open_questions"),
    ]:
        items = args.get(field, [])
        if items:
            existing = scope_data.get(key, [])
            scope_data[key] = existing + items
            added.append(f"{len(items)} {key}")

    path.write_text(_json.dumps(scope_data, indent=2), encoding="utf-8")

    reqs = len(scope_data.get("requirements", []))
    constraints = len(scope_data.get("constraints", []))
    _progress(f"[{agent_id}] scope updated: +{', '.join(added) if added else 'nothing'}")
    return (
        f"Scope updated. Now {reqs} requirements, {constraints} constraints. "
        f"Added: {', '.join(added) if added else 'nothing new'}."
    )


def _handle_read_scope(block: Any, agent_id: str) -> str:
    """Read the current scope contract."""
    import json as _json  # noqa: PLC0415

    path = _scope_path()
    if not path.exists():
        return "No scope exists yet. Call create-scope first."

    scope_data = _json.loads(path.read_text(encoding="utf-8"))
    _progress(f"[{agent_id}] read scope")
    return _json.dumps(scope_data, indent=2)


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


async def _handle_context_retrieve(
    block: Any, governance: GovernanceProvider | None, agent_id: str
) -> str:
    """Recall a window of original content that a reversible compressor elided.

    Retrieval reads back content already delivered to this agent (compressed), so it is not
    a privilege escalation — no grantable scope gates it. We still route through governance
    with an empty scope set so the recall is recorded in the audit trail. Returns a ranged
    window so a large original can be paged without re-hitting the result-truncation cap.
    """
    import json as _json  # noqa: PLC0415

    from swarmkit_runtime.compression import get_original  # noqa: PLC0415

    args = block.tool_input
    if isinstance(args, str):
        try:
            args = _json.loads(args)
        except (ValueError, TypeError):
            args = {}
    if not isinstance(args, dict):
        args = {}

    ref = str(args.get("ref", "")).strip()
    if not ref:
        return "[context_retrieve] error: 'ref' is required"

    if governance is not None:
        try:
            decision = await governance.evaluate_action(
                agent_id=agent_id,
                action=f"context:retrieve:{ref}",
                scopes_required=frozenset(),
                context={"ref": ref},
            )
            if not decision.allowed:
                return f"[context_retrieve] DENIED: {decision.reason}"
        except Exception:  # auditing must not block a legitimate read
            pass

    original = get_original(ref)
    if original is None:
        return (
            f"[context_retrieve] no stashed content for ref '{ref}' "
            "(unknown ref, or it expired earlier in this run)"
        )

    def _as_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return fallback

    offset = max(0, _as_int(args.get("offset", 0), 0))
    limit = max(1, _as_int(args.get("limit", 4000), 4000))
    window = original[offset : offset + limit]
    end = offset + len(window)
    remaining = len(original) - end
    _progress(f"  [{agent_id}] context_retrieve '{ref}' [{offset}:{end}] of {len(original)}")
    suffix = f"\n…[{remaining} more chars — call again with offset={end}]" if remaining > 0 else ""
    return (
        f"[context_retrieve ref={ref} bytes={len(original)} window={offset}:{end}]\n"
        f"{window}{suffix}"
    )


_DEFAULT_READ_PREFIXES = "read-,get-,list-,download-,describe-,explain-,render-"


def _read_prefixes() -> tuple[str, ...]:
    """Return read-tool prefixes, configurable via SWARMKIT_READ_TOOL_PREFIXES."""
    raw = os.environ.get("SWARMKIT_READ_TOOL_PREFIXES", _DEFAULT_READ_PREFIXES)
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _explicit_read_tools() -> frozenset[str]:
    """Return explicitly named read tools via SWARMKIT_READ_TOOLS."""
    raw = os.environ.get("SWARMKIT_READ_TOOLS", "")
    if not raw:
        return frozenset()
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _is_read_tool(tool_name: str) -> bool:
    """Check if a tool is read-only (fetch/read/list) vs search/write.

    Read-only tools get a higher per-tool limit because code analysis
    legitimately reads many files. Search tools keep the lower limit
    to prevent degenerate query spirals.

    Configurable via:
    - ``SWARMKIT_READ_TOOL_PREFIXES`` — prefix-based (default:
      ``read-,get-,list-,download-,describe-,explain-,render-``)
    - ``SWARMKIT_READ_TOOLS`` — explicit tool names (comma-separated,
      e.g. ``fetch-data,view-image,my-custom-reader``)
    """
    if tool_name in _explicit_read_tools():
        return True
    return tool_name.startswith(_read_prefixes())


def _get_tool_limit(tool_name: str, default_limit: int) -> int:
    """Return the per-tool call limit, higher for read-only tools.

    Configurable via:
    - ``SWARMKIT_MAX_PER_TOOL`` — limit for search/write tools (default 8)
    - ``SWARMKIT_MAX_PER_READ_TOOL`` — limit for read-only tools (default 50)
    - ``SWARMKIT_READ_TOOL_PREFIXES`` — which prefixes count as read-only
    """
    read_limit = int(os.environ.get("SWARMKIT_MAX_PER_READ_TOOL", "50"))
    if _is_read_tool(tool_name):
        return read_limit
    return default_limit


def _apply_tool_guards(
    results: list[ToolCallResult],
    call_counts: dict[str, int],
    max_per_tool: int,
    agent_id: str,
) -> tuple[list[ToolCallResult], bool]:
    """Apply per-tool call limits.

    Read-only tools (get-*, read-*) get a higher limit (default 50)
    since code analysis legitimately reads many files. Search tools
    keep the lower limit (default 8) to prevent degenerate spirals.

    Returns ``(modified_results, should_break)``.
    """
    hit_limit = False
    modified: list[ToolCallResult] = []

    for tr in results:
        call_counts[tr.tool_name] = call_counts.get(tr.tool_name, 0) + 1
        count = call_counts[tr.tool_name]
        limit = _get_tool_limit(tr.tool_name, max_per_tool)

        if count > limit:
            _progress(
                f"  [{agent_id}] tool '{tr.tool_name}' called {count} times — "
                f"forcing stop (limit: {limit})"
            )
            modified.append(
                ToolCallResult(
                    tool_use_id=tr.tool_use_id,
                    tool_name=tr.tool_name,
                    result=(
                        f"TOOL LIMIT: '{tr.tool_name}' has been called {count} times "
                        f"(limit: {limit}). STOP calling this tool. "
                        f"Write your findings from previous calls NOW. "
                        f"Do NOT retry, rephrase, or add more keywords."
                    ),
                    image_blocks=[],
                )
            )
            hit_limit = True
        else:
            modified.append(tr)

    return modified, hit_limit


async def _handle_skill_tool_calls(  # noqa: PLR0912, PLR0915
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
        _SCOPE_TOOLS = {
            "create-scope": _handle_create_scope,
            "update-scope": _handle_update_scope,
            "read-scope": _handle_read_scope,
        }
        if block.tool_name in _SCOPE_TOOLS:
            result_text = _SCOPE_TOOLS[block.tool_name](block, agent.id)
            results.append(
                ToolCallResult(
                    tool_use_id=block.tool_use_id or f"call_{len(results)}",
                    tool_name=block.tool_name,
                    result=result_text,
                    image_blocks=[],
                )
            )
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
        if block.tool_name == "context_retrieve":
            result_text = await _handle_context_retrieve(block, governance, agent.id)
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
        import time as _time  # noqa: PLC0415

        _tc_start = _time.monotonic()
        raw_result = await execute_skill(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=os.environ.get("SWARMKIT_MODEL") or model_name,
            mcp_manager=mcp_manager,
            governance=governance,
            agent_id=agent.id,
        )
        _tc_dur = int((_time.monotonic() - _tc_start) * 1000)
        if isinstance(raw_result, tuple):
            text_result, images = raw_result
        else:
            text_result, images = raw_result, []
        # Read-side context compression (opt-in, off by default). Per-surface policy keyed
        # by tool name. Applied here so the agent's context holds the compact form. Never on
        # the audit log (recorded separately) or the inter-agent contract.
        text_result = maybe_compress_tool_result(text_result or "", block.tool_name)
        _record_tool_call(
            tool_name=block.tool_name,
            arguments=block.tool_input if isinstance(block.tool_input, dict) else {},
            result_length=len(text_result or ""),
            duration_ms=_tc_dur,
        )
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
    *,
    tool_model_name: str | None = None,
    tool_model_provider: ModelProviderProtocol | None = None,
) -> str | dict[str, Any]:
    """Run the multi-turn tool loop until final text or turn limit.

    Keeps calling the model with tool results until it produces a final
    text response (no more tool calls) or we hit the turn limit. Includes
    nudging for incomplete responses.
    """
    _max_tool_turns = int(os.environ.get("SWARMKIT_MAX_TOOL_TURNS", "50"))
    _max_per_tool = int(os.environ.get("SWARMKIT_MAX_PER_TOOL", "8"))
    loop_messages = list(messages)
    current_response = response
    current_results = tool_results
    _tool_call_counts: dict[str, int] = {}
    _loop_provider = tool_model_provider or model_provider
    _loop_model = tool_model_name or model_name
    if _loop_model != model_name:
        _progress(f"  [{agent.id}] tool model: {_loop_model.rsplit('/', 1)[-1]}")

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
        follow_up = _build_completion_request(
            _loop_model, loop_messages, system_prompt, tools, agent
        )
        current_response = await _loop_provider.complete(follow_up)
        _record_tool_loop_tokens(agent.id, _loop_model, current_response)

        state_change = _check_for_state_change(current_response, agent, agent.id, None)
        if state_change is not None:
            return state_change

        next_results = await _handle_skill_tool_calls(
            current_response,
            agent,
            _loop_provider,
            _loop_model,
            mcp_manager,
            governance,
        )

        if next_results is not None:
            next_results, _hit_limit = _apply_tool_guards(
                next_results,
                _tool_call_counts,
                _max_per_tool,
                agent.id,
            )
            if _hit_limit:
                for tr in next_results:
                    tool_result_blocks.append(
                        ContentBlock(
                            type="tool_result",
                            tool_use_id=tr.tool_use_id,
                            tool_result=tr.result,
                        )
                    )
                loop_messages.append(
                    Message(role="assistant", content=list(current_response.content)),
                )
                loop_messages.append(
                    Message(role="user", content=tool_result_blocks),
                )
                break

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
                    _loop_model, loop_messages, system_prompt, tools, agent
                )
                current_response = await _loop_provider.complete(nudge_req)
                _record_tool_loop_tokens(agent.id, model_name, current_response)
                state_change = _check_for_state_change(current_response, agent, agent.id, None)
                if state_change is not None:
                    return state_change
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
    if text and text != "(no response)" and not _looks_incomplete(text):
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

    from ._output_schema import get_effective_output_schema  # noqa: PLC0415

    effective_schema = get_effective_output_schema(agent)
    if effective_schema:
        import json as _json  # noqa: PLC0415

        schema_str = _json.dumps(effective_schema, indent=2)
        loop_messages.append(
            Message(
                role="user",
                content=(
                    "STOP. Do NOT call any more tools.\n\n"
                    "Produce your final output as valid JSON matching this schema:\n"
                    f"```json\n{schema_str}\n```\n"
                    "Include ONLY facts from the tools you already called. "
                    "If you found nothing useful, return "
                    '{"findings": [], "not_found": ["<what you searched for>"]}.'
                ),
            ),
        )
    else:
        loop_messages.append(
            Message(
                role="user",
                content=(
                    "STOP. Do NOT call any more tools. Do NOT describe what you "
                    "planned to do next. Do NOT write 'Let me...' or 'I need to...' "
                    "or any planning language.\n\n"
                    "Write ONLY what you actually found from the tools you already "
                    "called. Summarize the RESULTS you received — specific data, "
                    "names, numbers, timestamps, and findings.\n\n"
                    "If you found nothing useful, say 'No relevant data found.' "
                    "Do NOT fabricate or speculate.\n\n"
                    "Start your response with: '## Analysis'"
                ),
            ),
        )
    synthesis_req = _build_completion_request(model_name, loop_messages, system_prompt, [], agent)
    synthesis_response = await model_provider.complete(synthesis_req)
    _record_tool_loop_tokens(agent.id, model_name, synthesis_response)
    text = _extract_text(synthesis_response)

    if text and not _looks_incomplete(text):
        return text

    _progress(f"  [{agent.id}] first synthesis attempt failed, retrying with minimal prompt...")
    minimal_messages = loop_messages[-6:]
    if effective_schema:
        import json as _json2  # noqa: PLC0415

        minimal_messages.append(
            Message(
                role="user",
                content=(
                    "Your previous response was empty or incomplete. "
                    "Return valid JSON with your findings NOW. "
                    "Even partial findings are better than nothing.\n"
                    f"Schema: {_json2.dumps(effective_schema)}"
                ),
            ),
        )
    else:
        minimal_messages.append(
            Message(
                role="user",
                content=(
                    "Your previous response was empty. Write a summary "
                    "of what you found. Even partial results are useful. "
                    "Start with the most important finding."
                ),
            ),
        )
    retry_req = _build_completion_request(model_name, minimal_messages, system_prompt, [], agent)
    retry_response = await model_provider.complete(retry_req)
    _record_tool_loop_tokens(agent.id, model_name, retry_response)
    return _extract_text(retry_response) or "(analysis incomplete — tool limit reached)"
