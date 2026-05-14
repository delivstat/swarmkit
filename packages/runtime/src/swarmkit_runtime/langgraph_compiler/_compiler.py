"""Core compiler: ResolvedTopology → compiled LangGraph StateGraph.

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.langgraph_compiler._skill_executor import execute_skill
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Message,
    MockModelProvider,
    ToolSpec,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology
from swarmkit_runtime.review._hitl import prompt_human_review
from swarmkit_runtime.skills._output_validator import (
    format_correction_prompt,
    validate_all_skill_output,
)

from ._state import SwarmState

_MAX_OUTPUT_RETRIES = 2
_TRUST_DENY_THRESHOLD = 0.2


def compile_topology(
    topology: ResolvedTopology,
    *,
    model_provider: ModelProviderProtocol | None = None,
    provider_registry: ProviderRegistry | None = None,
    governance: GovernanceProvider,
    mcp_manager: Any = None,
    checkpointer: Any = None,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compile a resolved topology into a runnable LangGraph graph.

    Each agent becomes a node. The root node is the entry point.
    Children are reachable via ``delegate_to_<child>`` tool calls that
    the root's model produces. The graph runs until the root returns a
    text response (no more delegation).

    Pass ``provider_registry`` for per-agent model resolution (each agent
    resolves to its own provider based on ``model.provider``). Pass
    ``model_provider`` as a shortcut when all agents share one provider.
    Pass ``mcp_manager`` for MCP tool execution.
    Pass ``checkpointer`` for state persistence (resume after HITL defer).
    """
    graph: StateGraph[Any] = StateGraph(SwarmState)
    agents = _collect_agents(topology.root)

    for agent in agents.values():
        agent_provider = _resolve_agent_provider(agent, provider_registry, model_provider)
        node_fn = _build_agent_node(
            agent,
            agent_provider,
            governance,
            agents,
            mcp_manager,
            provider_registry,
        )
        graph.add_node(agent.id, node_fn)

    graph.add_edge(START, topology.root.id)
    _add_routing_edges(graph, topology.root, agents)

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# ---- agent collection ---------------------------------------------------


def _resolve_agent_provider(
    agent: ResolvedAgent,
    registry: ProviderRegistry | None,
    fallback: ModelProviderProtocol | None,
) -> ModelProviderProtocol:
    """Resolve the model provider for a single agent.

    Uses the agent's ``model.provider`` field to look up in the registry.
    Falls back to the explicit ``fallback`` provider if no registry is
    given or the provider isn't found.
    """
    if registry is not None:
        provider_id = os.environ.get("SWARMKIT_PROVIDER") or (agent.model or {}).get("provider")
        if provider_id:
            provider = registry.get(provider_id)
            if provider is not None:
                return provider
    if fallback is not None:
        return fallback
    return MockModelProvider()


def _collect_agents(root: ResolvedAgent) -> dict[str, ResolvedAgent]:
    agents: dict[str, ResolvedAgent] = {}

    def walk(agent: ResolvedAgent) -> None:
        agents[agent.id] = agent
        for child in agent.children:
            walk(child)

    walk(root)
    return agents


# ---- extracted node helpers ----------------------------------------------


async def _check_governance(
    agent_id: str,
    agent: ResolvedAgent,
    governance: GovernanceProvider,
) -> dict[str, Any] | None:
    """Evaluate governance policy; return denial state dict or None if allowed."""
    iam = agent.iam or {}
    scopes_required = frozenset(iam.get("base_scope", []))

    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action="agent:execute",
        scopes_required=scopes_required,
    )

    if not decision.allowed:
        await governance.record_event(
            AuditEvent(
                event_type="policy.denied",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={
                    "action": "agent:execute",
                    "reason": decision.reason,
                    "scopes_denied": sorted(decision.scopes_denied),
                },
            )
        )
        return {
            "current_agent": agent_id,
            "agent_results": {agent_id: f"DENIED: {decision.reason}"},
            "messages": [
                AIMessage(
                    content=f"[{agent_id}] DENIED: {decision.reason}",
                    name=agent_id,
                )
            ],
            "output": f"DENIED: {decision.reason}",
        }
    return None


async def _check_trust(
    agent_id: str,
    agent: ResolvedAgent,
    governance: GovernanceProvider,
) -> dict[str, Any] | None:
    """Evaluate trust score; return denial state dict or None if trusted."""
    trust = await governance.get_trust_score(agent_id=agent_id)
    if trust.score < _TRUST_DENY_THRESHOLD:
        await governance.record_event(
            AuditEvent(
                event_type="trust.denied",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={
                    "score": trust.score,
                    "tier": trust.tier,
                },
            )
        )
        return {
            "current_agent": agent_id,
            "agent_results": {agent_id: f"DENIED: trust score {trust.score} below threshold"},
            "messages": [
                AIMessage(
                    content=f"[{agent_id}] DENIED: trust score too low ({trust.score})",
                    name=agent_id,
                )
            ],
            "output": f"DENIED: trust score {trust.score} below threshold",
        }
    if trust.tier == "degraded":
        await governance.record_event(
            AuditEvent(
                event_type="trust.degraded",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={"score": trust.score, "tier": trust.tier},
            )
        )
    return None


_MAX_RESULT_CHARS = int(os.environ.get("SWARMKIT_MAX_RESULT_CHARS", "3000"))


def _truncate_result(text: str, max_chars: int = 0) -> str:
    """Truncate a tool result to keep context manageable.

    Keeps the first and last portions so the model sees both the
    beginning (often headers/structure) and end (often summary/totals).
    """
    limit = max_chars or _MAX_RESULT_CHARS
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... ({len(text)} chars total, truncated for context) ...\n\n"
        + text[-half:]
    )


def _build_completion_request(
    model_name: str,
    messages: list[Message] | tuple[Message, ...],
    system_prompt: str | None,
    tools: list[ToolSpec],
    agent: ResolvedAgent,
) -> CompletionRequest:
    """Build a CompletionRequest -- single source for the repeated construction."""
    return CompletionRequest(
        model=model_name,
        messages=tuple(messages),
        system=system_prompt,
        tools=tuple(tools) if tools else None,
        temperature=(agent.model or {}).get("temperature"),
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


def _safe_parse_json(tool_name: str, response: Any, agent: Any) -> dict[str, object]:
    """Extract tool call inputs from the response for audit logging."""
    for block in getattr(response, "content", ()):
        if getattr(block, "tool_name", None) == tool_name:
            tool_input = getattr(block, "tool_input", None)
            if isinstance(tool_input, dict):
                return dict(tool_input)
            if isinstance(tool_input, str):
                try:
                    parsed = json.loads(tool_input)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                return {"raw_input": tool_input[:1000]}
    return {}


def _make_result(agent_id: str, result_text: str) -> dict[str, Any]:
    """Build the standard return dict for a completed agent."""
    return {
        "current_agent": agent_id,
        "agent_results": {agent_id: result_text},
        "messages": [AIMessage(content=result_text, name=agent_id)],
        "output": result_text,
    }


def _log_verbose_request(
    agent_id: str,
    model_name: str,
    tools: list[ToolSpec],
    messages: list[Message],
) -> None:
    """Log request details when SWARMKIT_VERBOSE is set."""
    print(f"\n--- [{agent_id}] calling {model_name} ---", file=sys.stderr)
    print(f"  tools: {[t.name for t in tools]}", file=sys.stderr)
    print(f"  input: {messages[-1].content[:200]}...", file=sys.stderr)


def _log_verbose_response(response: CompletionResponse) -> None:
    """Log response details when SWARMKIT_VERBOSE is set."""
    tool_calls = [b.tool_name for b in response.content if hasattr(b, "tool_name") and b.tool_name]
    text_parts = [b.text[:100] for b in response.content if hasattr(b, "text") and b.text]
    print(f"  tool_calls: {tool_calls}", file=sys.stderr)
    print(f"  text: {text_parts}", file=sys.stderr)


async def _record_completion(
    governance: GovernanceProvider,
    agent_id: str,
    role: str,
    result_text: str,
    start: datetime,
) -> None:
    """Record the agent.completed audit event."""
    end = datetime.now(tz=UTC)
    duration_ms = int((end - start).total_seconds() * 1000)
    await governance.record_event(
        AuditEvent(
            event_type="agent.completed",
            agent_id=agent_id,
            timestamp=end,
            payload={
                "result_length": len(result_text),
                "duration_ms": duration_ms,
                "role": role,
            },
            topology_id=agent_id,
        )
    )


async def _run_tool_loop(
    response: CompletionResponse,
    agent: ResolvedAgent,
    messages: list[Message],
    tools: list[ToolSpec],
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
    _max_tool_turns = int(os.environ.get("SWARMKIT_MAX_TOOL_TURNS", "25"))
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
                        "  [nudge: response looks incomplete, prompting to continue]",
                        file=sys.stderr,
                    )
                loop_messages.append(
                    Message(role="assistant", content=text),
                )
                loop_messages.append(
                    Message(
                        role="user",
                        content=(
                            "You described what you plan to do but "
                            "didn't do it. Call the tools now — use "
                            "read-file-lines to read the actual code."
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
                "This is your final response — synthesize all findings into a coherent answer."
            ),
        ),
    )
    synthesis_req = _build_completion_request(model_name, loop_messages, system_prompt, [], agent)
    synthesis_response = await model_provider.complete(synthesis_req)
    return _extract_text(synthesis_response) or "(analysis incomplete — tool limit reached)"


async def _dispatch_response(  # noqa: PLR0912
    response: CompletionResponse,
    agent: ResolvedAgent,
    agent_id: str,
    messages: list[Message],
    tools: list[ToolSpec],
    model_name: str,
    system_prompt: str | None,
    model_provider: ModelProviderProtocol,
    mcp_manager: Any,
    governance: GovernanceProvider,
    verbose: str,
    all_agents: dict[str, ResolvedAgent] | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> dict[str, Any] | tuple[CompletionResponse, list[Message]]:
    """Run the retry loop: delegation, tool-loop, or text-with-retry.

    Returns either a final state dict (delegation / tool-loop) or a
    ``(response, messages)`` tuple when the model produced text only.
    """
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

        delegations = _extract_delegation(response, agent)
        if delegations:
            if len(delegations) == 1:
                child_id, task_text = delegations[0]
                return {
                    "current_agent": child_id,
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
                child = _cm[cid]
                child_state: SwarmState = {
                    "input": task,
                    "messages": [HumanMessage(content=task, name=agent_id)],
                    "agent_results": {},
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

            if verbose:
                names = [d[0] for d in delegations]
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

            return {
                "current_agent": agent_id,
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
            synth_text = await _run_tool_loop(
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
            return _make_result(agent_id, synth_text)

        # Model returned text -- retry if agent has many skill tools
        # (indicating it should be researching, not just talking).
        # Agents with only write-notes or 1-2 utility tools should NOT
        # be nudged — they're coordinators that synthesize via text.
        _UTILITY_TOOLS = {"write-notes", "read-context"}
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


async def _finalize_text_result(
    response: CompletionResponse,
    messages: list[Message],
    agent: ResolvedAgent,
    agent_id: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    governance: GovernanceProvider,
    agent_results: dict[str, Any],
    completed_children: set[str],
) -> str:
    """Validate output, apply child fallback, return final text."""
    result_text = _extract_text(response)
    outputs_schema = _get_outputs_schema(agent)
    if outputs_schema and result_text != "(no response)":
        result_text = await _validate_and_correct(
            result_text,
            outputs_schema,
            model_provider=model_provider,
            model_name=model_name,
            system_prompt=system_prompt,
            messages=messages,
            governance=governance,
            agent_id=agent_id,
        )

    if result_text == "(no response)" and completed_children:
        child_texts = [
            str(agent_results[cid]) for cid in completed_children if cid in agent_results
        ]
        result_text = "\n\n".join(child_texts)

    return result_text


# ---- node construction --------------------------------------------------


def _build_agent_node(
    agent: ResolvedAgent,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any = None,
    provider_registry: ProviderRegistry | None = None,
) -> Any:
    """Build an async node function for one agent."""
    drift_observer = _create_drift_observer(agent)

    async def node_fn(state: SwarmState) -> dict[str, Any]:
        agent_id = agent.id
        _start = datetime.now(tz=UTC)
        await governance.record_event(
            AuditEvent(
                event_type="agent.started",
                agent_id=agent_id,
                timestamp=_start,
                payload={"role": agent.role},
            )
        )

        # ---- governance + trust gates --------------------------------
        denial = await _check_governance(agent_id, agent, governance)
        if denial is not None:
            return denial

        denial = await _check_trust(agent_id, agent, governance)
        if denial is not None:
            return denial

        # ---- message + tool construction -----------------------------
        messages = _build_prompt_messages(agent, state)
        tools = _build_tools(agent, mcp_manager=mcp_manager)
        agent_results = state.get("agent_results", {})
        completed_children = _get_completed_children(agent, agent_results)

        model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")
        system_prompt = _build_system_prompt(agent, tools)
        _verbose = os.environ.get("SWARMKIT_VERBOSE", "")

        if _verbose:
            _log_verbose_request(agent_id, model_name, tools, messages)

        request = _build_completion_request(model_name, messages, system_prompt, tools, agent)
        response = await model_provider.complete(request)

        if _verbose:
            _log_verbose_response(response)

        # ---- retry / delegation / tool-loop dispatch -----------------
        result = await _dispatch_response(
            response,
            agent,
            agent_id,
            messages,
            tools,
            model_name,
            system_prompt,
            model_provider,
            mcp_manager,
            governance,
            _verbose,
            all_agents=all_agents,
            provider_registry=provider_registry,
        )
        if isinstance(result, dict):
            await _record_completion(
                governance,
                agent_id,
                agent.role,
                result.get("output", ""),
                _start,
            )
            return result

        # Text path -- result is (final_response, final_messages).
        final_response, final_messages = result
        result_text = await _finalize_text_result(
            final_response,
            final_messages,
            agent,
            agent_id,
            model_provider,
            model_name,
            system_prompt,
            governance,
            agent_results,
            completed_children,
        )
        await _record_completion(governance, agent_id, agent.role, result_text, _start)

        if drift_observer and drift_observer.config.enabled:
            if not drift_observer.anchor_text:
                drift_observer.set_anchor(state.get("input", ""))
            drift_result = drift_observer.observe(
                step=len(drift_observer.history), output=result_text
            )
            await _handle_drift_result(drift_result, drift_observer, governance, agent_id, messages)

        return _make_result(agent_id, result_text)

    node_fn.__name__ = f"agent_{agent.id}"
    return node_fn


# ---- intent drift --------------------------------------------------------


def _create_drift_observer(agent: ResolvedAgent) -> Any:
    """Create an IntentObserver for the agent if monitoring is configured."""
    from swarmkit_runtime.drift import IntentMonitoringConfig, IntentObserver  # noqa: PLC0415

    raw_config = getattr(agent, "intent_monitoring", None)
    if raw_config is None:
        return None

    if isinstance(raw_config, dict):
        config = IntentMonitoringConfig.from_dict(raw_config)
    else:
        config = IntentMonitoringConfig.from_dict(
            {
                "enabled": getattr(raw_config, "enabled", False),
                "threshold": getattr(raw_config, "threshold", 0.75),
                "on_drift": getattr(raw_config, "on_drift", "log"),
            }
        )

    if not config.enabled:
        return None
    return IntentObserver(config)


async def _handle_drift_result(
    drift_result: Any,
    observer: Any,
    governance: GovernanceProvider,
    agent_id: str,
    messages: list[Message],
) -> None:
    """Record drift and apply strategy (log/warn/nudge)."""
    await governance.record_event(
        AuditEvent(
            event_type="intent.drift",
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload={
                "drift_score": drift_result.score,
                "threshold": drift_result.threshold,
                "exceeded": drift_result.exceeded,
                "action": drift_result.action_taken,
            },
        )
    )

    if drift_result.exceeded and drift_result.action_taken == "nudge":
        nudge_msg = observer.get_nudge_message()
        messages.append(Message(role="user", content=nudge_msg))
        if os.environ.get("SWARMKIT_VERBOSE"):
            print(
                f"  [drift] score={drift_result.score:.4f} "
                f"threshold={drift_result.threshold} → nudge injected",
                file=sys.stderr,
            )
    elif drift_result.exceeded and os.environ.get("SWARMKIT_VERBOSE"):
        print(
            f"  [drift] score={drift_result.score:.4f} "
            f"threshold={drift_result.threshold} → {drift_result.action_taken}",
            file=sys.stderr,
        )


# ---- routing / edges ----------------------------------------------------


def _add_routing_edges(
    graph: StateGraph[Any],
    root: ResolvedAgent,
    agents: dict[str, ResolvedAgent],
) -> None:
    """Wire conditional edges so delegation tool calls route to children."""

    def walk(agent: ResolvedAgent) -> None:
        if not agent.children:
            graph.add_edge(agent.id, _parent_of(agent, root) or END)
            return

        child_ids = {c.id for c in agent.children}
        destinations: dict[str, str] = {cid: cid for cid in child_ids}
        destinations[agent.id] = agent.id
        destinations["__end__"] = END

        def router(
            state: SwarmState,
            *,
            _agent_id: str = agent.id,
            _dests: dict[str, str] = destinations,
        ) -> str:
            results = state.get("agent_results", {})
            agent_result = results.get(_agent_id, "")

            if isinstance(agent_result, str) and agent_result.startswith("__delegated__:"):
                target = agent_result.split(":", 1)[1]
                if target in _dests:
                    return target

            if agent_result == "__delegated_parallel__":
                return _agent_id

            return "__end__"

        graph.add_conditional_edges(agent.id, router, destinations)  # type: ignore[arg-type]

        for child in agent.children:
            parent_id = agent.id
            graph.add_edge(child.id, parent_id)
            walk(child)

    walk(root)


def _parent_of(agent: ResolvedAgent, root: ResolvedAgent) -> str | None:
    """Find the parent id of an agent in the tree. Returns None for root."""

    def search(node: ResolvedAgent, target_id: str) -> str | None:
        for child in node.children:
            if child.id == target_id:
                return node.id
            found = search(child, target_id)
            if found:
                return found
        return None

    return search(root, agent.id)


# ---- prompt construction -------------------------------------------------


def _build_system_prompt(agent: ResolvedAgent, tools: list[ToolSpec]) -> str | None:
    """Build the system prompt, injecting tool-use instructions when needed.

    The topology author's system prompt is preserved. The compiler appends
    a brief instruction block listing available tools so the model knows
    to call them instead of describing what it would do in text.
    """
    base = (agent.prompt or {}).get("system", "") or ""
    if not tools:
        return base or None

    delegation_tools = [t for t in tools if t.name.startswith("delegate_to_")]
    skill_tools = [t for t in tools if not t.name.startswith("delegate_to_")]

    parts = [base.rstrip()] if base else []
    parts.append(
        "\nYou have the following tools available. "
        "Use them to act - do not describe what you would do."
    )

    if delegation_tools:
        names = ", ".join(f"`{t.name}`" for t in delegation_tools)
        parts.append(
            f"DELEGATION (MANDATORY for root agents): You MUST delegate by calling "
            f"one of: {names}. Do NOT answer questions directly — always delegate."
        )

    if skill_tools:
        names = ", ".join(f"`{t.name}`" for t in skill_tools)
        parts.append(f"Skills available: {names}")

    return "\n".join(parts)


# ---- tool / message construction ----------------------------------------


def _build_prompt_messages(  # noqa: PLR0912
    agent: ResolvedAgent,
    state: SwarmState,
) -> list[Message]:
    """Build the message list for an agent's model call.

    If child agents have produced results (delegation completed), the
    prompt includes those results so the agent can synthesise a final
    answer instead of re-delegating.
    """
    messages: list[Message] = []
    agent_results = state.get("agent_results", {})

    child_results = {
        cid: agent_results[cid]
        for cid in [c.id for c in agent.children]
        if cid in agent_results
        and isinstance(agent_results[cid], str)
        and not agent_results[cid].startswith("__delegated__:")
    }

    if child_results:
        results_text = "\n\n".join(f"[{cid}]:\n{result}" for cid, result in child_results.items())
        messages.append(
            Message(
                role="user",
                content=(
                    f"Original request: {state.get('input', '')}\n\n"
                    f"Your workers have produced the following results:\n\n{results_text}\n\n"
                    f"Present the workers' output directly to the user as the final response. "
                    f"Do not add commentary about the workers or the delegation process — "
                    f"just deliver the result as if you produced it yourself."
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
]


def _looks_incomplete(text: str) -> bool:
    """Check if a response contains planning language without actual results."""
    lower = text.lower().strip()
    return len(lower) < 200 and any(m in lower for m in _INCOMPLETE_MARKERS)


def _build_tools(agent: ResolvedAgent, mcp_manager: Any = None) -> list[ToolSpec]:
    """Map an agent's executable skills + children to ToolSpec objects.

    ``llm_prompt`` and ``mcp_tool`` skills are included. ``composed``
    skills are excluded (panel aggregation is invoked differently).

    For ``mcp_tool`` skills the ``inputSchema`` published by the MCP
    server is forwarded as the tool's ``input_schema`` so the LLM sees
    the correct parameter names rather than inventing its own.
    """
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

    for child in agent.children:
        tools.append(
            ToolSpec(
                name=f"delegate_to_{child.id}",
                description=f"Delegate a task to {child.id} (role={child.role})",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The task to delegate"}
                    },
                    "required": ["task"],
                },
            )
        )

    return tools


# ---- skill execution ----------------------------------------------------


_MAX_TOOL_CALLS_PER_TURN = int(os.environ.get("SWARMKIT_MAX_TOOLS", "10"))


@dataclass
class ToolCallResult:
    """A single tool call and its result."""

    tool_use_id: str
    tool_name: str
    result: str
    image_blocks: list[ContentBlock] = field(default_factory=list)


async def _handle_skill_tool_calls(
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
        skill = skill_map.get(block.tool_name)
        if skill is None:
            continue
        input_text = ""
        if isinstance(block.tool_input, dict):
            input_text = json.dumps(block.tool_input)
        elif isinstance(block.tool_input, str):
            input_text = block.tool_input
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


# ---- DAG execution ------------------------------------------------------


def _has_dag_deps(agent: ResolvedAgent) -> bool:
    """Check if any child agent has depends_on declarations."""
    return any(c.depends_on for c in agent.children)


async def _run_dag(
    agent: ResolvedAgent,
    agent_id: str,
    task: str,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any,
    provider_registry: ProviderRegistry | None,
    verbose: str,
) -> dict[str, str]:
    """Execute child agents in dependency order. Returns {child_id: result}."""
    import asyncio as _asyncio  # noqa: PLC0415

    children = {c.id: c for c in agent.children}
    results: dict[str, str] = {}
    completed: set[str] = set()

    def _ready(child: ResolvedAgent) -> bool:
        if child.id in completed:
            return False
        return all(d in completed for d in child.depends_on)

    max_rounds = len(children) + 1
    for _round in range(max_rounds):
        runnable = [c for c in children.values() if _ready(c)]
        if not runnable:
            break

        if verbose:
            names = [c.id for c in runnable]
            print(f"  [dag round {_round + 1}: running {names}]", file=sys.stderr)

        async def _exec_child(
            child: ResolvedAgent,
            _children: dict[str, ResolvedAgent] = children,
        ) -> tuple[str, str]:
            dep_context = ""
            if child.depends_on:
                parts = []
                for dep_id in child.depends_on:
                    if dep_id in results:
                        parts.append(f"[{dep_id}]:\n{results[dep_id]}")
                if parts:
                    dep_context = (
                        "Previous agents produced these results:\n\n" + "\n\n".join(parts) + "\n\n"
                    )

            child_input = f"{dep_context}Your task: {task}"
            child_state: SwarmState = {
                "input": child_input,
                "messages": [HumanMessage(content=child_input, name=agent_id)],
                "agent_results": {},
                "current_agent": child.id,
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
            return (child.id, result_state.get("output", "(no response)"))

        batch = await _asyncio.gather(*[_exec_child(c) for c in runnable])
        for cid, result in batch:
            results[cid] = result
            completed.add(cid)

    return results


# ---- response parsing ---------------------------------------------------


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


def _extract_text(response: CompletionResponse) -> str:
    parts: list[str] = []
    for block in response.content:
        if block.type == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts) or "(no response)"


# ---- output governance --------------------------------------------------


def _get_outputs_schema(agent: ResolvedAgent) -> dict[str, Any] | None:
    """Return the JSON Schema for the agent's first skill with outputs, or None."""
    for skill in agent.skills:
        outputs = getattr(skill.raw, "outputs", None)
        if outputs is not None:
            return dict(outputs) if not isinstance(outputs, dict) else outputs
    return None


async def _validate_and_correct(
    result_text: str,
    outputs_schema: dict[str, Any],
    *,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    messages: list[Message],
    governance: GovernanceProvider,
    agent_id: str,
) -> str:
    """Validate skill output against JSON Schema; re-prompt on failure.

    Tries to parse the result as JSON and validate against the schema.
    On failure, sends field-specific errors back to the model for
    targeted correction (up to ``_MAX_OUTPUT_RETRIES`` attempts).
    """
    for attempt in range(_MAX_OUTPUT_RETRIES + 1):
        try:
            parsed = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            if attempt == _MAX_OUTPUT_RETRIES:
                return result_text
            correction = (
                "Your response must be valid JSON matching the output schema. "
                "Please return a valid JSON object."
            )
            result_text = await _retry_with_correction(
                correction, model_provider, model_name, system_prompt, messages
            )
            continue

        errors = validate_all_skill_output(parsed, outputs_schema)
        if not errors:
            await governance.record_event(
                AuditEvent(
                    event_type="output.validated",
                    agent_id=agent_id,
                    timestamp=datetime.now(tz=UTC),
                    payload={"attempt": attempt + 1, "valid": True},
                )
            )
            return result_text

        if attempt == _MAX_OUTPUT_RETRIES:
            await governance.record_event(
                AuditEvent(
                    event_type="output.validation_failed",
                    agent_id=agent_id,
                    timestamp=datetime.now(tz=UTC),
                    payload={
                        "attempts": attempt + 1,
                        "errors": [{"field": e.field, "message": e.message} for e in errors],
                    },
                )
            )

            if sys.stdin.isatty():
                decision = prompt_human_review(
                    agent_id=agent_id,
                    skill_id="output-validation",
                    output=parsed,
                    verdict=None,
                    reason=f"Validation failed after {attempt + 1} attempts: "
                    + "; ".join(f"{e.field}: {e.message}" for e in errors),
                )
                if decision == "approved":
                    return result_text

            return result_text

        correction = format_correction_prompt(errors)
        result_text = await _retry_with_correction(
            correction, model_provider, model_name, system_prompt, messages
        )

    return result_text


async def _retry_with_correction(
    correction: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    messages: list[Message],
) -> str:
    """Re-prompt the model with a correction message."""
    retry_messages = [*messages, Message(role="user", content=correction)]
    response = await model_provider.complete(
        CompletionRequest(
            model=model_name,
            messages=tuple(retry_messages),
            system=system_prompt,
        )
    )
    return _extract_text(response)
