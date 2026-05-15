"""Core compiler: ResolvedTopology → compiled LangGraph StateGraph.

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology
from swarmkit_runtime.trace import AgentStep, RunTrace

from ._dag import _has_dag_deps, _run_dag  # noqa: F401
from ._delegation import _dispatch_response
from ._drift import _create_drift_observer, _handle_drift_result
from ._helpers import (
    _check_governance,
    _check_trust,
    _log_verbose_request,
    _log_verbose_response,
    _make_result,
    _progress,
    _record_completion,
)
from ._output_gov import _finalize_text_result, _validate_and_correct  # noqa: F401
from ._prompts import (
    _build_completion_request,
    _build_prompt_messages,
    _build_system_prompt,
    _build_tools,
    _get_completed_children,
)
from ._state import SwarmState

_active_trace: RunTrace | None = None
_current_parent_agent: str | None = None


def set_active_trace(trace: RunTrace | None) -> None:
    """Set the active run trace for the current execution."""
    global _active_trace  # noqa: PLW0603
    _active_trace = trace


def compile_topology(
    topology: ResolvedTopology,
    *,
    model_provider: ModelProviderProtocol | None = None,
    provider_registry: ProviderRegistry | None = None,
    governance: GovernanceProvider,
    mcp_manager: Any = None,
    checkpointer: Any = None,
    workspace_root: Any = None,
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
    Pass ``workspace_root`` for task plan disk persistence.
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
            workspace_root=workspace_root,
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


# ---- node construction --------------------------------------------------


def _build_agent_node(  # noqa: PLR0915
    agent: ResolvedAgent,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any = None,
    provider_registry: ProviderRegistry | None = None,
    workspace_root: Any = None,
) -> Any:
    """Build an async node function for one agent."""
    drift_observer = _create_drift_observer(agent)

    async def node_fn(state: SwarmState) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
        global _current_parent_agent  # noqa: PLW0603
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

        # ---- task plan execution (structured delegation v2) -----------
        _agent_result = state.get("agent_results", {}).get(agent_id, "")
        _is_task_plan = isinstance(_agent_result, str) and _agent_result.startswith("__task_plan_")
        if _is_task_plan and _agent_result in (
            "__task_plan_created__",
            "__task_plan_updated__",
            "__task_plan_executing__",
        ):
            from swarmkit_runtime.langgraph_compiler._task_executor import (  # noqa: PLC0415
                execute_task_batch,
                get_plan_from_state,
            )

            plan = get_plan_from_state(state)
            if plan:
                runnable = plan.get_runnable_tasks()
                if runnable:
                    batch_result = await execute_task_batch(
                        plan,
                        agent,
                        agent_id,
                        model_provider,
                        governance,
                        all_agents or {},
                        mcp_manager,
                        provider_registry,
                        workspace_root=workspace_root,
                    )
                    # If more tasks remain, let coordinator review
                    # by falling through to LLM call on next re-entry
                    return batch_result

        # ---- message + tool construction -----------------------------
        messages = _build_prompt_messages(agent, state)
        tools = _build_tools(agent, mcp_manager=mcp_manager)
        agent_results = state.get("agent_results", {})
        completed_children = _get_completed_children(agent, agent_results)
        all_children_done = len(completed_children) == len(agent.children) and agent.children
        _max_delegations = int(os.environ.get("SWARMKIT_MAX_DELEGATIONS_PER_CHILD", "2"))
        if all_children_done:
            tools = [t for t in tools if not t.name.startswith("delegate_to_")]
        elif completed_children and _max_delegations > 0:
            delegation_counts: dict[str, int] = state.get("delegation_counts", {})
            exhausted = {
                cid
                for cid in completed_children
                if delegation_counts.get(cid, 0) >= _max_delegations
            }
            if exhausted:
                tools = [
                    t
                    for t in tools
                    if not (
                        t.name.startswith("delegate_to_")
                        and t.name[len("delegate_to_") :] in exhausted
                    )
                ]

        model_name = os.environ.get("SWARMKIT_MODEL") or (agent.model or {}).get("name", "mock")
        system_prompt = _build_system_prompt(agent, tools)
        _verbose = os.environ.get("SWARMKIT_VERBOSE", "")

        _short_model = model_name.rsplit("/", 1)[-1] if "/" in model_name else model_name
        _progress(f"[{agent_id}] thinking... ({_short_model})")

        if _verbose:
            _log_verbose_request(agent_id, model_name, tools, messages)

        request = _build_completion_request(model_name, messages, system_prompt, tools, agent)
        response = await model_provider.complete(request)

        _trace_step = AgentStep(
            agent_id=agent_id,
            model=model_name,
            parent_agent=_current_parent_agent,
            role=agent.role,
            start_time=_start.timestamp(),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        _prev_parent = _current_parent_agent
        _current_parent_agent = agent_id

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
            state=state,
            all_agents=all_agents,
            provider_registry=provider_registry,
        )
        if isinstance(result, dict):
            _elapsed = (datetime.now(tz=UTC) - _start).total_seconds()
            _output = result.get("output", "")
            if _output and not _output.startswith("__delegated"):
                _progress(f"[{agent_id}] done ({_elapsed:.1f}s)")
            await _record_completion(
                governance,
                agent_id,
                agent.role,
                _output,
                _start,
            )
            delegated_to: list[str] = []
            for k, v in result.get("agent_results", {}).items():
                if isinstance(v, str) and v.startswith("__delegated__:"):
                    delegated_to.append(v.split(":", 1)[1])
                elif isinstance(v, str) and v == "__delegated_parallel__":
                    delegated_to.extend(
                        ck for ck in result.get("agent_results", {}) if ck not in {k, agent_id}
                    )
            _trace_step.delegations = delegated_to
            _trace_step.end_time = datetime.now(tz=UTC).timestamp()
            _trace_step.duration_ms = int((_trace_step.end_time - _trace_step.start_time) * 1000)
            if _active_trace is not None:
                _active_trace.add_step(_trace_step)
            _current_parent_agent = _prev_parent
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
        _elapsed = (datetime.now(tz=UTC) - _start).total_seconds()
        _progress(f"[{agent_id}] done ({_elapsed:.1f}s)")
        await _record_completion(governance, agent_id, agent.role, result_text, _start)

        _trace_step.end_time = datetime.now(tz=UTC).timestamp()
        _trace_step.duration_ms = int((_trace_step.end_time - _trace_step.start_time) * 1000)
        _trace_step.result_length = len(result_text)
        if _active_trace is not None:
            _active_trace.add_step(_trace_step)
        _current_parent_agent = _prev_parent

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


# ---- routing / edges ----------------------------------------------------


def _add_routing_edges(
    graph: StateGraph[Any],
    root: ResolvedAgent,
    agents: dict[str, ResolvedAgent],
) -> None:
    """Wire conditional edges so delegation tool calls route to children."""

    def walk(agent: ResolvedAgent, parent_id: str | None = None) -> None:
        if not agent.children:
            graph.add_edge(agent.id, parent_id or END)
            return

        child_ids = {c.id for c in agent.children}
        destinations: dict[str, str] = {cid: cid for cid in child_ids}
        destinations[agent.id] = agent.id
        # Route to parent when done (or END if this is root)
        _done_target = parent_id or END
        destinations["__done__"] = _done_target

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

            if isinstance(agent_result, str) and agent_result.startswith("__task_plan_"):
                return _agent_id

            return "__done__"

        graph.add_conditional_edges(agent.id, router, destinations)  # type: ignore[arg-type]

        for child in agent.children:
            if child.children:
                # Non-leaf child has its own conditional edges that
                # route back to this agent via __done__. A fixed edge
                # would conflict and cause duplicate parent invocations.
                walk(child, parent_id=agent.id)
            else:
                graph.add_edge(child.id, agent.id)
                walk(child, parent_id=agent.id)

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
