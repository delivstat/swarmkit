"""Core compiler: ResolvedTopology → compiled LangGraph StateGraph.

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    Message,
    MockModelProvider,
    ToolSpec,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology

from ._state import SwarmState


def compile_topology(
    topology: ResolvedTopology,
    *,
    model_provider: ModelProviderProtocol | None = None,
    provider_registry: ProviderRegistry | None = None,
    governance: GovernanceProvider,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compile a resolved topology into a runnable LangGraph graph.

    Each agent becomes a node. The root node is the entry point.
    Children are reachable via ``delegate_to_<child>`` tool calls that
    the root's model produces. The graph runs until the root returns a
    text response (no more delegation).

    Pass ``provider_registry`` for per-agent model resolution (each agent
    resolves to its own provider based on ``model.provider``). Pass
    ``model_provider`` as a shortcut when all agents share one provider.
    """
    graph: StateGraph[Any] = StateGraph(SwarmState)
    agents = _collect_agents(topology.root)

    for agent in agents.values():
        agent_provider = _resolve_agent_provider(agent, provider_registry, model_provider)
        node_fn = _build_agent_node(agent, agent_provider, governance, agents)
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


def _build_agent_node(
    agent: ResolvedAgent,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
) -> Any:
    """Build an async node function for one agent."""

    async def node_fn(state: SwarmState) -> dict[str, Any]:
        agent_id = agent.id
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

        messages = _build_prompt_messages(agent, state)
        tools = _build_tools(agent)

        model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")
        system_prompt = (agent.prompt or {}).get("system")

        request = CompletionRequest(
            model=model_name,
            messages=tuple(messages),
            system=system_prompt,
            tools=tuple(tools) if tools else None,
            temperature=(agent.model or {}).get("temperature"),
        )

        response = await model_provider.complete(request)

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

        # No delegation — agent produced a final text response
        result_text = _extract_text(response)

        await governance.record_event(
            AuditEvent(
                event_type="agent.completed",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={"result_length": len(result_text)},
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


# ---- tool / message construction ----------------------------------------


def _build_prompt_messages(
    agent: ResolvedAgent,
    state: SwarmState,
) -> list[Message]:
    """Build the message list for an agent's model call."""
    messages: list[Message] = []

    task = state.get("input", "")

    last_human = None
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            last_human = msg.content
            break

    content = last_human or task
    messages.append(Message(role="user", content=str(content)))
    return messages


def _build_tools(agent: ResolvedAgent) -> list[ToolSpec]:
    """Map an agent's skills + children to ToolSpec objects."""
    tools: list[ToolSpec] = []

    for skill in agent.skills:
        desc = getattr(skill, "description", "") or skill.id
        tools.append(ToolSpec(name=skill.id, description=desc))

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
    parts: list[str] = []
    for block in response.content:
        if block.type == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts) or "(no response)"
