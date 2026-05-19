"""Delegation dispatch: routing, parallel delegation, and task-plan handling.

Contains ``_dispatch_response`` (the retry loop that decides whether to
delegate, run tools, or return text) and delegation-extraction helpers.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import CompletionResponse, Message
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent

from ._helpers import (
    _extract_text,
    _log_verbose_response,
    _make_result,
    _progress,
    _safe_parse_json,
)
from ._prompts import _build_completion_request
from ._state import SwarmState
from ._task_plan_handler import _handle_task_plan_tools

_UTILITY_TOOLS = {"write-notes", "read-context"}


def _find_invalid_delegations(
    response: CompletionResponse,
    agent: ResolvedAgent,
) -> list[str]:
    """Find delegate_to_X tool calls where X is not a valid child."""
    child_ids = {c.id for c in agent.children}
    invalid: list[str] = []
    for block in response.content:
        if block.type != "tool_use" or not block.tool_name:
            continue
        if block.tool_name.startswith("delegate_to_"):
            target = block.tool_name[len("delegate_to_") :]
            if target not in child_ids:
                invalid.append(block.tool_name)
    return invalid


def _extract_delegation(
    response: CompletionResponse,
    agent: ResolvedAgent,
) -> list[tuple[str, str]]:
    """Extract all delegate_to_<child> tool calls. Returns [(child_id, task), ...]."""
    child_ids = {c.id for c in agent.children}
    delegations: list[tuple[str, str]] = []
    seen: set[str] = set()
    for block in response.content:
        if block.type != "tool_use" or not block.tool_name:
            continue
        if block.tool_name.startswith("delegate_to_"):
            target = block.tool_name[len("delegate_to_") :]
            if target in child_ids and target not in seen:
                seen.add(target)
                task = ""
                if isinstance(block.tool_input, dict):
                    task = block.tool_input.get("task", "")
                elif isinstance(block.tool_input, str):
                    try:
                        parsed = json.loads(block.tool_input)
                        task = parsed.get("task", block.tool_input)
                    except (json.JSONDecodeError, AttributeError):
                        task = block.tool_input
                delegations.append((target, str(task)))
    return delegations


async def _dispatch_response(  # noqa: PLR0911, PLR0912, PLR0915
    response: CompletionResponse,
    agent: ResolvedAgent,
    agent_id: str,
    messages: list[Message],
    tools: list[Any],
    model_name: str,
    system_prompt: str | None,
    model_provider: ModelProviderProtocol,
    mcp_manager: Any,
    governance: GovernanceProvider,
    verbose: str,
    state: SwarmState | None = None,
    all_agents: dict[str, ResolvedAgent] | None = None,
    provider_registry: ProviderRegistry | None = None,
    planning_config: Any = None,
    synthesis_config: Any = None,
) -> dict[str, Any] | tuple[CompletionResponse, list[Message]]:
    """Run the retry loop: delegation, tool-loop, or text-with-retry.

    Returns either a final state dict (delegation / tool-loop) or a
    ``(response, messages)`` tuple when the model produced text only.
    """
    from ._dag import _has_dag_deps, _run_dag  # noqa: PLC0415
    from ._tool_loop import _handle_skill_tool_calls, _run_tool_loop  # noqa: PLC0415

    _max_retries = int(os.environ.get("SWARMKIT_AGENT_RETRIES", "2"))
    for _attempt in range(_max_retries + 1):
        # Check for DAG-based execution first
        if _has_dag_deps(agent):
            delegations = _extract_delegation(response, agent)
            if delegations:
                task_text = delegations[0][1] or str(messages[-1].content)[:500]
                if verbose:
                    print("  [dag mode: running children in dependency order]", file=sys.stderr)
                dag_results = await _run_dag(
                    agent,
                    agent_id,
                    task_text,
                    model_provider,
                    governance,
                    all_agents or {},
                    mcp_manager,
                    provider_registry,
                    verbose,
                )
                merged_messages = []
                for cid, result in dag_results.items():
                    merged_messages.append(AIMessage(content=result, name=cid))
                return {
                    "current_agent": agent_id,
                    "agent_results": {
                        agent_id: "__delegated_parallel__",
                        **dag_results,
                    },
                    "messages": merged_messages,
                }

        invalid_delegations = _find_invalid_delegations(response, agent)
        if invalid_delegations:
            invalid_names = ", ".join(invalid_delegations)
            valid_names = ", ".join(f"delegate_to_{c.id}" for c in agent.children)
            if verbose:
                print(
                    f"  [invalid delegation: {invalid_names}]",
                    file=sys.stderr,
                )
            messages = [
                *messages,
                Message(role="assistant", content=list(response.content)),
                Message(
                    role="user",
                    content=(
                        f"Invalid tool call: {invalid_names}. "
                        f"You cannot delegate to yourself or to agents that are not your workers. "
                        f"Your valid delegation tools are: {valid_names}. "
                        f"If you have gathered enough information, write your analysis now "
                        f"without calling any tools."
                    ),
                ),
            ]
            request = _build_completion_request(model_name, messages, system_prompt, tools, agent)
            response = await model_provider.complete(request)
            continue

        task_plan_result = _handle_task_plan_tools(
            response,
            agent,
            agent_id,
            state,
            planning_config=planning_config,
            synthesis_config=synthesis_config,
        )
        if task_plan_result is not None:
            return task_plan_result

        delegations = _extract_delegation(response, agent)
        if delegations:
            if len(delegations) == 1:
                child_id, task_text = delegations[0]
                _task_preview = (task_text or "")[:120].replace("\n", " ")
                _progress(f"[{agent_id}] -> delegating to {child_id}: {_task_preview}")
                _prev_counts: dict[str, int] = state.get("delegation_counts", {}) if state else {}
                _new_counts = {**_prev_counts, child_id: _prev_counts.get(child_id, 0) + 1}
                return {
                    "current_agent": child_id,
                    "delegation_counts": _new_counts,
                    "agent_results": {
                        agent_id: f"__delegated__:{child_id}",
                    },
                    "messages": [
                        AIMessage(
                            content=f"[{agent_id}] Delegating to {child_id}: {task_text}",
                            name=agent_id,
                        ),
                        HumanMessage(content=task_text, name=agent_id),
                    ],
                }

            # Multiple delegations — run child nodes in parallel
            import asyncio as _asyncio  # noqa: PLC0415

            child_map = {c.id: c for c in agent.children}

            async def _run_child(
                cid: str,
                task: str,
                _cm: dict[str, ResolvedAgent] = child_map,
            ) -> tuple[str, str]:
                from ._compiler import _build_agent_node, _resolve_agent_provider  # noqa: PLC0415

                child = _cm[cid]
                child_state: SwarmState = {
                    "input": task,
                    "messages": [HumanMessage(content=task, name=agent_id)],
                    "agent_results": {},
                    "delegation_counts": {},
                    "task_plan": {},
                    "current_agent": cid,
                    "output": "",
                }
                child_provider = _resolve_agent_provider(
                    child,
                    provider_registry,
                    model_provider,
                )
                child_fn = _build_agent_node(
                    child,
                    child_provider,
                    governance,
                    all_agents or {},
                    mcp_manager,
                    provider_registry,
                )
                result_state = await child_fn(child_state)
                return (cid, result_state.get("output", "(no response)"))

            names = [d[0] for d in delegations]
            _progress(f"[{agent_id}] -> delegating in parallel: {', '.join(names)}")
            if verbose:
                print(
                    f"  [parallel delegation: {names}]",
                    file=sys.stderr,
                )

            tasks = [_run_child(cid, task) for cid, task in delegations]
            child_results = await _asyncio.gather(*tasks)

            merged_results = dict(child_results)
            merged_messages = []
            for cid, _task in delegations:
                merged_messages.append(
                    AIMessage(
                        content=f"[{agent_id}] Delegated to {cid}",
                        name=agent_id,
                    ),
                )
            for cid, result in child_results:
                merged_messages.append(
                    AIMessage(content=result, name=cid),
                )

            _par_prev: dict[str, int] = state.get("delegation_counts", {}) if state else {}
            _par_counts = {**_par_prev}
            for cid, _ in delegations:
                _par_counts[cid] = _par_counts.get(cid, 0) + 1
            return {
                "current_agent": agent_id,
                "delegation_counts": _par_counts,
                "agent_results": {
                    agent_id: "__delegated_parallel__",
                    **merged_results,
                },
                "messages": merged_messages,
            }

        tool_results = await _handle_skill_tool_calls(
            response, agent, model_provider, model_name, mcp_manager, governance
        )
        if tool_results is not None:
            for tr in tool_results:
                await governance.record_event(
                    AuditEvent(
                        event_type="skill.executed",
                        agent_id=agent_id,
                        timestamp=datetime.now(tz=UTC),
                        skill_id=tr.tool_name,
                        payload={
                            "tools_called": len(tool_results),
                            "inputs": _safe_parse_json(tr.tool_name, response, agent),
                            "outputs": {"result": tr.result[:1000]},
                        },
                    )
                )
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
                return loop_result
            return _make_result(agent_id, loop_result)

        # Model returned text -- retry if the response is empty and
        # the agent has delegation or planning tools it should use.
        _resp_text = _extract_text(response)
        _is_empty = _resp_text in ("(no response)", "") or not _resp_text.strip()
        _has_delegation = any(
            t.name.startswith("delegate_to_") or t.name == "create-task-plan" for t in tools
        )
        if _is_empty and _has_delegation and _attempt < _max_retries:
            if verbose:
                print(
                    f"  [retry {_attempt + 1}: empty response, nudging to delegate]",
                    file=sys.stderr,
                )
            messages = [
                *messages,
                Message(role="assistant", content=_resp_text),
                Message(
                    role="user",
                    content=(
                        "You returned an empty response. You MUST use your tools. "
                        "Call delegate_to or create-task-plan now."
                    ),
                ),
            ]
            request = _build_completion_request(
                model_name,
                messages,
                system_prompt,
                tools,
                agent,
            )
            response = await model_provider.complete(request)
            if verbose:
                _log_verbose_response(response)
            continue

        # Retry if agent has many skill tools it should be using.
        skill_tools = [
            t
            for t in tools
            if not t.name.startswith("delegate_to_") and t.name not in _UTILITY_TOOLS
        ]
        if skill_tools and _attempt < _max_retries:
            if verbose:
                print(
                    f"  [retry {_attempt + 1}: model returned text, nudging to use tools]",
                    file=sys.stderr,
                )
            messages = [
                *messages,
                Message(role="assistant", content=_extract_text(response)),
                Message(
                    role="user",
                    content=(
                        "You have tools available. Do NOT describe what you would do — "
                        "call the tools now. Use the tool_use format to execute actions."
                    ),
                ),
            ]
            request = _build_completion_request(model_name, messages, system_prompt, tools, agent)
            response = await model_provider.complete(request)
            if verbose:
                _log_verbose_response(response)
            continue
        break

    return (response, messages)
