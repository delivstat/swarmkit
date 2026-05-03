"""Core compiler: ResolvedTopology → compiled LangGraph StateGraph.

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
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
from swarmkit_runtime.skills import impl_get
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
    """
    graph: StateGraph[Any] = StateGraph(SwarmState)
    agents = _collect_agents(topology.root)

    for agent in agents.values():
        agent_provider = _resolve_agent_provider(agent, provider_registry, model_provider)
        node_fn = _build_agent_node(agent, agent_provider, governance, agents, mcp_manager)
        graph.add_node(agent.id, node_fn)

    graph.add_edge(START, topology.root.id)
    _add_routing_edges(graph, topology.root, agents)

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


# ---- node construction --------------------------------------------------


def _build_agent_node(  # noqa: PLR0915
    agent: ResolvedAgent,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any = None,
) -> Any:
    """Build an async node function for one agent."""

    async def node_fn(state: SwarmState) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
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

        messages = _build_prompt_messages(agent, state)
        tools = _build_tools(agent, mcp_manager=mcp_manager)

        # Remove delegation tools if children already returned results —
        # the agent should synthesise, not re-delegate.
        agent_results = state.get("agent_results", {})
        completed_children = {
            c.id
            for c in agent.children
            if c.id in agent_results
            and isinstance(agent_results[c.id], str)
            and not str(agent_results[c.id]).startswith("__delegated__:")
        }
        if completed_children:
            tools = [t for t in tools if not t.name.startswith("delegate_to_")]

        model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")
        system_prompt = _build_system_prompt(agent, tools)

        request = CompletionRequest(
            model=model_name,
            messages=tuple(messages),
            system=system_prompt,
            tools=tuple(tools) if tools else None,
            temperature=(agent.model or {}).get("temperature"),
        )

        _verbose = os.environ.get("SWARMKIT_VERBOSE", "")
        if _verbose:
            import sys  # noqa: PLC0415

            print(f"\n--- [{agent_id}] calling {model_name} ---", file=sys.stderr)
            print(f"  tools: {[t.name for t in tools]}", file=sys.stderr)
            print(f"  input: {messages[-1].content[:200]}...", file=sys.stderr)

        response = await model_provider.complete(request)

        if _verbose:
            tool_calls = [
                b.tool_name for b in response.content if hasattr(b, "tool_name") and b.tool_name
            ]
            text_parts = [b.text[:100] for b in response.content if hasattr(b, "text") and b.text]
            print(f"  tool_calls: {tool_calls}", file=sys.stderr)
            print(f"  text: {text_parts}", file=sys.stderr)

        _max_retries = int(os.environ.get("SWARMKIT_AGENT_RETRIES", "2"))
        for _attempt in range(_max_retries + 1):
            # Check for delegation tool calls
            delegation = _extract_delegation(response, agent)
            if delegation:
                child_id, task_text = delegation
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

            # Check for skill tool calls
            tool_results = await _handle_skill_tool_calls(
                response, agent, model_provider, model_name, mcp_manager, governance
            )
            if tool_results is not None:
                await governance.record_event(
                    AuditEvent(
                        event_type="skill.executed",
                        agent_id=agent_id,
                        timestamp=datetime.now(tz=UTC),
                        payload={"tools_called": len(tool_results)},
                    )
                )

                # Multi-turn tool loop: keep calling the model with tool
                # results until it produces a final text response (no more
                # tool calls) or we hit the turn limit.
                _max_tool_turns = int(os.environ.get("SWARMKIT_MAX_TOOL_TURNS", "8"))
                loop_messages = list(messages)
                current_response = response
                current_results = tool_results

                for _turn in range(_max_tool_turns):
                    assistant_blocks = list(current_response.content)
                    tool_result_blocks = [
                        ContentBlock(
                            type="tool_result",
                            tool_use_id=tr.tool_use_id,
                            tool_result=tr.result,
                        )
                        for tr in current_results
                    ]
                    loop_messages.append(
                        Message(role="assistant", content=assistant_blocks),
                    )
                    loop_messages.append(
                        Message(role="user", content=tool_result_blocks),
                    )
                    follow_up = CompletionRequest(
                        model=model_name,
                        messages=tuple(loop_messages),
                        system=system_prompt,
                        tools=tuple(tools) if tools else None,
                        temperature=(agent.model or {}).get("temperature"),
                    )
                    if _verbose:
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
                        # Model returned text without tool calls. Check if
                        # it looks incomplete (planning language instead of
                        # a final answer) and nudge it to continue.
                        text = _extract_text(current_response)
                        if _turn < _max_tool_turns - 1 and _looks_incomplete(text):
                            if _verbose:
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
                            nudge_req = CompletionRequest(
                                model=model_name,
                                messages=tuple(loop_messages),
                                system=system_prompt,
                                tools=tuple(tools) if tools else None,
                                temperature=(agent.model or {}).get("temperature"),
                            )
                            current_response = await model_provider.complete(
                                nudge_req,
                            )
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

                synth_text = _extract_text(current_response)
                return {
                    "current_agent": agent.id,
                    "agent_results": {agent_id: synth_text},
                    "messages": [AIMessage(content=synth_text, name=agent_id)],
                    "output": synth_text,
                }

            # Model returned text instead of tool calls.
            # Retry only if agent has skill tools (not just delegation).
            skill_tools = [t for t in tools if not t.name.startswith("delegate_to_")]
            if skill_tools and _attempt < _max_retries:
                if _verbose:
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
                request = CompletionRequest(
                    model=model_name,
                    messages=tuple(messages),
                    system=system_prompt,
                    tools=tuple(tools) if tools else None,
                    temperature=(agent.model or {}).get("temperature"),
                )
                response = await model_provider.complete(request)
                if _verbose:
                    tc = [
                        b.tool_name
                        for b in response.content
                        if hasattr(b, "tool_name") and b.tool_name
                    ]
                    print(f"  tool_calls: {tc}", file=sys.stderr)
                continue
            break

        # No delegation or skill calls — agent produced a final text response.
        # If skills have output schemas, validate + auto-correct.
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

        # Fallback: if the model returned nothing useful but children
        # have results, pass through the child output directly.
        if result_text == "(no response)" and completed_children:
            child_texts = [
                str(agent_results[cid]) for cid in completed_children if cid in agent_results
            ]
            result_text = "\n\n".join(child_texts)

        _end = datetime.now(tz=UTC)
        _duration_ms = int((_end - _start).total_seconds() * 1000)
        await governance.record_event(
            AuditEvent(
                event_type="agent.completed",
                agent_id=agent_id,
                timestamp=_end,
                payload={
                    "result_length": len(result_text),
                    "duration_ms": _duration_ms,
                    "role": agent.role,
                },
                topology_id=agent_id,
            )
        )

        return {
            "current_agent": agent.id,
            "agent_results": {agent_id: result_text},
            "messages": [AIMessage(content=result_text, name=agent_id)],
            "output": result_text,
        }

    node_fn.__name__ = f"agent_{agent.id}"
    return node_fn


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


def _build_prompt_messages(
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
        # Build conversation context for worker agents so they can see
        # prior findings and avoid redundant tool calls.
        conversation: list[Message] = []
        for msg in state.get("messages", []):
            if isinstance(msg, HumanMessage):
                conversation.append(Message(role="user", content=str(msg.content)))
            elif isinstance(msg, AIMessage) and msg.content:
                content = str(msg.content)
                if not content.startswith("__delegated__:"):
                    conversation.append(Message(role="assistant", content=content))

        task = state.get("input", "")
        last_human = None
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                last_human = msg.content
                break

        if conversation:
            messages.extend(conversation)
        else:
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
        impl_type = impl_get(impl, "type")
        if impl_type in _executable_types:
            desc = getattr(skill, "description", "") or skill.id
            input_schema: dict[str, Any] = {}
            if impl_type == "mcp_tool" and mcp_manager is not None:
                server_id = str(impl_get(impl, "server"))
                tool_name = str(impl_get(impl, "tool"))
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
        result = await execute_skill(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=os.environ.get("SWARMKIT_MODEL") or model_name,
            mcp_manager=mcp_manager,
            governance=governance,
            agent_id=agent.id,
        )
        results.append(
            ToolCallResult(
                tool_use_id=block.tool_use_id or f"call_{len(results)}",
                tool_name=block.tool_name,
                result=result or "(no result)",
            )
        )

    return results if results else None


# ---- response parsing ---------------------------------------------------


def _extract_delegation(
    response: CompletionResponse,
    agent: ResolvedAgent,
) -> tuple[str, str] | None:
    """If the response contains a delegate_to_<child> tool call, return (child_id, task)."""
    child_ids = {c.id for c in agent.children}
    for block in response.content:
        if block.type != "tool_use" or not block.tool_name:
            continue
        if block.tool_name.startswith("delegate_to_"):
            target = block.tool_name[len("delegate_to_") :]
            if target in child_ids:
                task = ""
                if isinstance(block.tool_input, dict):
                    task = block.tool_input.get("task", "")
                elif isinstance(block.tool_input, str):
                    try:
                        parsed = json.loads(block.tool_input)
                        task = parsed.get("task", block.tool_input)
                    except (json.JSONDecodeError, AttributeError):
                        task = block.tool_input
                return (target, str(task))
    return None


def _extract_text(response: CompletionResponse) -> str:
    return response.text or "(no response)"


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
