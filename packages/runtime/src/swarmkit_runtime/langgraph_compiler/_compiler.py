"""Core compiler: ResolvedTopology → compiled LangGraph StateGraph.

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.governance import AuditEvent, DecisionSkillBinding, GovernanceProvider
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology
from swarmkit_runtime.trace import AgentStep, RunTrace

from ._dag import _has_dag_deps, _run_dag  # noqa: F401
from ._decision_gate import evaluate_post_output
from ._delegation import _dispatch_response
from ._drift import _create_drift_observer, _handle_drift_result, _is_error_passthrough
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
from ._run_context import (
    current_parent_agent,
    reset_parent_agent,
    set_parent_agent,
)
from ._sentinels import (
    TASK_PLAN_ACTIVE,
    AgentStatus,
    delegated_child,
    is_delegated,
    is_task_plan_status,
)
from ._state import PlanningConfig, SwarmState

# Per-run active state. ContextVar (not a module global) so concurrent runs in one
# process — e.g. several jobs under `swarmkit serve` — don't clobber each other: asyncio
# copies the context when a task is created, so each run sees its own trace.
_active_trace_var: ContextVar[RunTrace | None] = ContextVar("swarmkit_active_trace", default=None)
# Parent-agent attribution is a ContextVar (see _run_context), not a module global — so
# concurrent delegated children attribute their parent correctly under asyncio.gather.


def set_active_trace(trace: RunTrace | None) -> None:
    """Set the active run trace for the current execution context."""
    _active_trace_var.set(trace)


def get_active_trace() -> RunTrace | None:
    """Return the active run trace for the current execution context, if any."""
    return _active_trace_var.get()


def compile_topology(
    topology: ResolvedTopology,
    *,
    model_provider: ModelProviderProtocol | None = None,
    provider_registry: ProviderRegistry | None = None,
    governance: GovernanceProvider,
    mcp_manager: Any = None,
    checkpointer: Any = None,
    workspace_root: Any = None,
    decision_skill_bindings: list[DecisionSkillBinding] | None = None,
    planning_config: PlanningConfig | None = None,
    synthesis_config: Any = None,
    memory_store: Any = None,
    governed_memory_store: Any = None,
    review_queue: Any = None,
    role_registry: Any = None,
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

    _bindings = decision_skill_bindings or []
    _planning = planning_config or PlanningConfig()
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
            decision_skill_bindings=_bindings,
            planning_config=_planning,
            synthesis_config=synthesis_config,
            memory_store=memory_store,
            governed_memory_store=governed_memory_store,
        )
        # In-node funnel gate: if the agent references a funnel and the gate deps are wired,
        # wrap the node so a live run produces -> judges -> parks for multi-party approval,
        # retrying the agent on failure. The structural invariant lives in the gate subgraph.
        if agent.funnel is not None and review_queue is not None:
            node_fn = _wrap_with_funnel_gate(
                node_fn,
                agent,
                topology.id,
                governance=governance,
                review_queue=review_queue,
                role_registry=role_registry,
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
    decision_skill_bindings: list[DecisionSkillBinding] | None = None,
    planning_config: PlanningConfig | None = None,
    synthesis_config: Any = None,
    memory_store: Any = None,
    governed_memory_store: Any = None,
) -> Any:
    """Build an async node function for one agent."""
    drift_observer = _create_drift_observer(agent)
    _ds_bindings = decision_skill_bindings or []
    _planning = planning_config or PlanningConfig()
    _synthesis = synthesis_config
    _memory = memory_store
    # This agent writes governed memory only if it carries the `governed-memory` persistence skill.
    _governed_memory = (
        governed_memory_store if any(s.id == "governed-memory" for s in agent.skills) else None
    )

    async def node_fn(state: SwarmState) -> dict[str, Any]:  # noqa: PLR0911, PLR0912, PLR0915
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

        # ---- pre_input decision skills (relevance gate) ----------------
        if _ds_bindings:
            user_input = state.get("input", "")
            if user_input:
                from swarmkit_runtime.langgraph_compiler._decision_gate import (  # noqa: PLC0415
                    evaluate_pre_input,
                )

                should_proceed, rejection, _ = await evaluate_pre_input(
                    agent_id=agent_id,
                    user_input=user_input,
                    bindings=_ds_bindings,
                    governance=governance,
                )
                if not should_proceed:
                    return _make_result(
                        agent_id,
                        rejection or "I can only help with questions in my domain.",
                    )

        # ---- workspace memory (pre_input context injection) ----------------
        if _memory and _ds_bindings:
            from swarmkit_runtime.memory._gate import memory_pre_input  # noqa: PLC0415

            memory_context = await memory_pre_input(
                agent_id=agent_id,
                user_input=state.get("input", ""),
                bindings=_ds_bindings,
                store=_memory,
            )
            if memory_context:
                _progress(f"  [{agent_id}] memory context injected")
                state = {**state, "input": f"{memory_context}\n\n{state.get('input', '')}"}

        # ---- executor dispatch (executor-abstraction §5) ---------------
        # `model` runs the tool-loop below, unchanged. Any other kind is a harness executor and
        # hands off to its runner. This sits BELOW the pre_input gates deliberately: a relevance
        # gate is about the input, which is executor-agnostic, and refusing after paying for a
        # harness run would be a strange way to decline. Memory context injection is above for the
        # same reason — the harness has to see what was injected.
        #
        # The gates used to sit below this dispatch, so a harness node reached NONE of them: a
        # `required: true` decision skill was computed, discarded, and the unvalidated output
        # returned as success. An executor chooses the mechanism, not the contract.
        if agent.executor.kind != "model":
            return await _run_harness_with_gates(
                agent,
                state,
                governance,
                agent_id=agent_id,
                bindings=_ds_bindings,
                workspace_root=workspace_root,
                model_provider=model_provider,
                mcp_manager=mcp_manager,
            )

        # ---- already-delegated fast path (resume scenarios) ------------
        _agent_result = state.get("agent_results", {}).get(agent_id, "")
        _child_has_task_plan = any(
            is_task_plan_status(state.get("agent_results", {}).get(c.id, ""))
            for c in agent.children
        )
        if is_delegated(_agent_result) and _child_has_task_plan:
            return {
                "current_agent": agent_id,
                "agent_results": {agent_id: _agent_result},
                "messages": [],
            }

        # ---- task plan execution (structured delegation v2) -----------
        if is_task_plan_status(_agent_result) and _agent_result in TASK_PLAN_ACTIVE:
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
                        decision_skill_bindings=_ds_bindings,
                        synthesis_config=_synthesis,
                        original_input=state.get("input", ""),
                        synthesizer_role=_planning.synthesizer_role,
                    )
                    # If more tasks remain, let coordinator review
                    # by falling through to LLM call on next re-entry
                    return batch_result

        # ---- message + tool construction -----------------------------
        messages = _build_prompt_messages(agent, state, planning_config=_planning)
        tools = _build_tools(
            agent,
            mcp_manager=mcp_manager,
            planning_config=_planning,
            synthesis_config=_synthesis,
        )
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
        _tool_model_name = (agent.model or {}).get("tool_model")
        _tool_provider_name = (agent.model or {}).get("tool_provider")
        _tool_model_provider = None
        if _tool_model_name and provider_registry and _tool_provider_name:
            _tool_model_provider = provider_registry.get(_tool_provider_name)
        elif _tool_model_name and provider_registry:
            _tool_model_provider = model_provider

        system_prompt = _build_system_prompt(
            agent,
            tools,
            planning_config=_planning,
            synthesis_config=_synthesis,
        )
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
            parent_agent=current_parent_agent(),
            role=agent.role,
            start_time=_start.timestamp(),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            cost_usd=response.usage.cost_usd,
        )
        _trace = get_active_trace()
        if _trace is not None:
            _trace.add_step(_trace_step)
        _parent_token = set_parent_agent(agent_id)

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
            planning_config=_planning,
            synthesis_config=_synthesis,
            tool_model_name=_tool_model_name,
            tool_model_provider=_tool_model_provider,
        )
        if isinstance(result, dict):
            _elapsed = (datetime.now(tz=UTC) - _start).total_seconds()
            _output = result.get("output", "")
            if _output and not (is_delegated(_output) or _output == AgentStatus.DELEGATED_PARALLEL):
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
                if is_delegated(v):
                    delegated_to.append(delegated_child(v))
                elif isinstance(v, str) and v == AgentStatus.DELEGATED_PARALLEL:
                    delegated_to.extend(
                        ck for ck in result.get("agent_results", {}) if ck not in {k, agent_id}
                    )
            _trace_step.delegations = delegated_to
            _trace_step.end_time = datetime.now(tz=UTC).timestamp()
            _trace_step.duration_ms = int((_trace_step.end_time - _trace_step.start_time) * 1000)
            reset_parent_agent(_parent_token)
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
        reset_parent_agent(_parent_token)

        if drift_observer and drift_observer.config.enabled:
            if not drift_observer.anchor_text:
                drift_observer.set_anchor(state.get("input", ""))
            # Skip drift scoring for error passthroughs — they are not
            # agent reasoning and would distort the drift signal.
            if not _is_error_passthrough(result_text):
                drift_result = drift_observer.observe(
                    step=len(drift_observer.history), output=result_text
                )
                await _handle_drift_result(
                    drift_result, drift_observer, governance, agent_id, messages
                )

        if _ds_bindings:
            from swarmkit_runtime.langgraph_compiler._task_executor import (  # noqa: PLC0415
                _make_retry_fn,
            )

            _retry = _make_retry_fn(state.get("input", ""), result_text, model_provider, agent)
            result_text, _ = await evaluate_post_output(
                agent_id=agent_id,
                output=result_text,
                bindings=_ds_bindings,
                governance=governance,
                retry_fn=_retry,
            )

        # ---- workspace memory (post_output insight extraction) --------
        if _memory and _ds_bindings:
            from swarmkit_runtime.memory._gate import memory_post_output  # noqa: PLC0415

            await memory_post_output(
                agent_id=agent_id,
                user_input=state.get("input", ""),
                agent_output=result_text,
                bindings=_ds_bindings,
                store=_memory,
                model_provider=model_provider,
                model_name=(agent.model or {}).get("name", "default"),
            )

        # ---- governed memory (post_output candidate write) ------------
        if _governed_memory is not None:
            from swarmkit_runtime.governed_memory._hook import (  # noqa: PLC0415
                governed_memory_post_output,
            )

            summary = await governed_memory_post_output(
                agent_id=agent_id, agent_output=result_text, store=_governed_memory
            )
            if summary["written"]:
                _progress(f"  [{agent_id}] governed memory: {summary['by_op']}")

        return _make_result(agent_id, result_text)

    node_fn.__name__ = f"agent_{agent.id}"
    return node_fn


def _harness_output_schema(agent: ResolvedAgent) -> dict[str, Any] | None:
    """The schema enforced on a harness node's output — an EXPLICIT one only.

    Deliberately not :func:`get_effective_output_schema`, which the model path uses. That falls back
    to the worker platform default (``{findings: [{fact, source}], …}``) for any ``role: worker``
    without an explicit schema. Applying it here would silently impose a findings-schema on every
    harness worker — `examples/sdlc-pipeline` alone has a `developer` archetype that is
    ``role: worker`` + ``kind: harness`` with no schema, and it produces a diff, not findings. Every
    run of it would start failing validation and burning full harness retries to satisfy a contract
    nobody wrote.

    The platform default exists for structured inter-agent communication between model workers in
    the delegation pattern. A harness node produces artifacts. So: enforce what the author actually
    declared, and nothing more.
    """
    if agent.output_schema_disabled:
        return None
    return dict(agent.output_schema) if agent.output_schema is not None else None


async def _enforce_harness_output_schema(
    text: str,
    schema: dict[str, Any],
    *,
    agent_id: str,
    governance: GovernanceProvider,
    reinvoke: Any,
    max_retries: int = 2,
) -> str:
    """Validate a harness result against its declared schema, correcting through the harness.

    Same shape as the model path's ``_validate_and_correct``, with the correction driven by the
    agent's own executor: a model asked to fix the JSON would be editing a description of work done
    in a sandbox it cannot reach. Field-specific errors go back as the correction, so the harness is
    told which fields are wrong rather than "try again".

    Bounded, and on exhaustion the text passes through ANNOTATED rather than silently — the whole
    point of this gap was output that failed a declared contract and looked fine.
    """
    from swarmkit_runtime.skills._output_validator import (  # noqa: PLC0415
        format_correction_prompt,
        validate_all_skill_output,
    )

    current = text
    errors: list[Any] = []
    for attempt in range(max_retries + 1):
        try:
            parsed = json.loads(current)
        except (json.JSONDecodeError, TypeError):
            errors = [
                type("FieldError", (), {"field": "(root)", "message": "output is not valid JSON"})()
            ]
        else:
            errors = (
                validate_all_skill_output(parsed, schema)
                if isinstance(parsed, dict)
                else [
                    type(
                        "FieldError",
                        (),
                        {"field": "(root)", "message": "output must be a JSON object"},
                    )()
                ]
            )
        if not errors:
            return current
        if attempt >= max_retries:
            break
        current = await reinvoke(format_correction_prompt(errors))

    await governance.record_event(
        AuditEvent(
            event_type="output.schema_violation",
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload={"errors": [f"{e.field}: {e.message}" for e in errors]},
        )
    )
    flags = "\n".join(f"  - {e.field}: {e.message}" for e in errors)
    return f"{current}\n\n---\nOUTPUT SCHEMA VIOLATIONS:\n{flags}"


async def _run_harness_with_gates(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    *,
    agent_id: str,
    bindings: list[DecisionSkillBinding],
    workspace_root: Any = None,
    model_provider: Any = None,
    mcp_manager: Any = None,
) -> dict[str, Any]:
    """Run a harness node and apply its ``post_output`` decision skills.

    A harness node is a NODE: everything the declarative layer says about a node is supposed to hold
    whichever executor runs it. `required: true` used to mean nothing here, silently.

    The retry re-invokes the **harness**, not a model. `_make_retry_fn` re-prompts a model with the
    previous output — "the agent doesn't re-run tools, it revises using data it already has" — which
    is wrong twice over for a harness: it needs a `model_provider` a harness agent may not have, and
    a harness's output is the product of work in a sandbox, so revising its *text* with some other
    model produces a description of a fix rather than the fix. Each retry is therefore a full
    harness run, bounded by the binding's own ``max_retries``. A cheap retry that cannot fix
    anything would be worse than none.
    """
    from swarmkit_runtime.langgraph_compiler._harness_node import (  # noqa: PLC0415
        run_harness_node,
    )

    async def _invoke(node_state: SwarmState) -> dict[str, Any]:
        return await run_harness_node(
            agent,
            node_state,
            governance,
            workspace_root=workspace_root,
            model_provider=model_provider,
            mcp_manager=mcp_manager,
        )

    result = await _invoke(state)

    # A harness that FAILED is not a non-conforming output. Gating it would ask a decision skill to
    # judge an error string, and a `required` skill would then retry — paying for a full harness run
    # to fix a sandbox that could not start. Return the failure as the failure it is.
    if result.get("node_errors"):
        return result

    original_input = str(state.get("input", "") or "")

    async def _reinvoke(feedback: str, preamble: str) -> str:
        """Re-run the harness with feedback appended to the task statement."""
        revised = f"{original_input}\n\n{preamble}\n{feedback}"
        retry_state: SwarmState = {**state, "input": revised}
        retried = await _invoke(retry_state)
        # A harness that fails on the retry leaves the previous text in place, so an infrastructure
        # failure is never mistaken for the agent's revised answer.
        if retried.get("node_errors"):
            return str(result.get("output", "") or "")
        return str(retried.get("output", "") or "")

    # `output_schema` was ignored entirely on this path, so a harness agent had neither a schema
    # constraint nor a post-hoc check — the two independent mechanisms that would each have caught
    # a non-conforming output. This restores the first; the decision skills below are the second.
    text = str(result.get("output", "") or "")
    schema = _harness_output_schema(agent)
    if schema is not None and text:
        text = await _enforce_harness_output_schema(
            text,
            schema,
            agent_id=agent_id,
            governance=governance,
            reinvoke=lambda fb: _reinvoke(
                fb,
                "Your previous output did not match the required output schema. "
                "Return the COMPLETE corrected result:",
            ),
        )
        if text != result.get("output"):
            result = _replace_node_text(result, agent_id, text)

    if not bindings:
        return result

    from swarmkit_runtime.langgraph_compiler._decision_gate import (  # noqa: PLC0415
        evaluate_post_output,
    )

    checked, _ = await evaluate_post_output(
        agent_id=agent_id,
        output=str(result.get("output", "") or ""),
        bindings=bindings,
        governance=governance,
        retry_fn=lambda fb: _reinvoke(
            fb,
            "A governance review of your previous attempt requires changes before this can be "
            "accepted. Address this feedback and produce the COMPLETE corrected result:",
        ),
    )
    if checked == result.get("output"):
        return result
    return _replace_node_text(result, agent_id, checked)


def _replace_node_text(result: dict[str, Any], agent_id: str, text: str) -> dict[str, Any]:
    """Put revised text everywhere the node's answer flows.

    Output, the per-agent result AND the message: leaving the original in any of them would hand
    the next node the text that was just rejected.
    """
    from langchain_core.messages import AIMessage  # noqa: PLC0415

    return {
        **result,
        "output": text,
        "agent_results": {agent_id: text},
        "messages": [AIMessage(content=text, name=agent_id)],
    }


def _wrap_with_funnel_gate(
    node_fn: Any,
    agent: ResolvedAgent,
    topology_id: str,
    *,
    governance: GovernanceProvider,
    review_queue: Any,
    role_registry: Any,
) -> Any:
    """Wrap a gated agent's node so a live run routes its output through its funnel.

    The wrapped node runs the funnel gate (validate -> judge -> multi-party approve, with
    a bounded retry that re-invokes the agent carrying the gate critique). The run advances
    only on human approval; a rejection surfaces as a gate-rejected agent result. The
    ``approve`` layer blocks on ``resolve_multiparty`` — the same engine used elsewhere.
    """
    from swarmkit_runtime.langgraph_compiler._gate_funnel import (  # noqa: PLC0415
        run_agent_funnel_gate,
    )

    funnel = agent.funnel
    assert funnel is not None

    async def gated_node(state: SwarmState) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def produce(critique: str | None) -> str:
            run_state = state
            if critique:
                revised = f"{state.get('input', '')}\n\n[Revise per gate critique]: {critique}"
                run_state = {**state, "input": revised}
            out = await node_fn(run_state)
            captured["out"] = out
            return str((out.get("agent_results") or {}).get(agent.id, ""))

        def diff_source() -> str | None:
            # The gated node's produced diff (a harness executor surfaces it; a model node won't).
            diff = captured.get("out", {}).get("diff")
            return diff if isinstance(diff, str) else None

        gate_state = await run_agent_funnel_gate(
            dict(funnel.spec),
            produce=produce,
            governance=governance,
            review_queue=review_queue,
            role_registry=role_registry,
            topology_id=topology_id,
            agent_id=agent.id,
            diff_source=diff_source,
        )
        out: dict[str, Any] = captured.get("out", {})
        if gate_state.get("outcome") != "approved":
            critique = (gate_state.get("provenance") or {}).get("critique") or ""
            return _make_result(agent.id, f"[GATE REJECTED] {critique}".strip())
        return out

    gated_node.__name__ = f"gated_{agent.id}"
    return gated_node


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
        destinations[AgentStatus.DONE] = _done_target

        def router(
            state: SwarmState,
            *,
            _agent_id: str = agent.id,
            _dests: dict[str, str] = destinations,
        ) -> str:
            results = state.get("agent_results", {})
            agent_result = results.get(_agent_id, "")

            if is_delegated(agent_result):
                target = delegated_child(agent_result)
                if target in _dests:
                    return target

            if agent_result == AgentStatus.DELEGATED_PARALLEL:
                return _agent_id

            if is_task_plan_status(agent_result):
                return _agent_id

            return AgentStatus.DONE

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
