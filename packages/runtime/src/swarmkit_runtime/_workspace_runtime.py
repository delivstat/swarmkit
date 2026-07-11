"""WorkspaceRuntime — the backend that CLI, HTTP server, and web UI call into.

Owns the full execution lifecycle: resolve workspace → build providers →
build governance → wire MCP → compile topology → invoke graph → close.
The CLI is a thin interface over this; ``swarmkit serve`` (M9) and the
v1.1 web UI will be additional interfaces over the same class.

See ``design/details/workspace-runtime.md`` (to be written) and the
architectural decision in ``memory/feedback_cli_architecture.md``.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.audit import AuditProvider, SqlAuditProvider, audit_provider_for_path
from swarmkit_runtime.governance import (
    DecisionSkillBinding,
    DecisionSkillResult,
    GovernanceProvider,
    merge_decision_skills,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.mcp import (
    MCPClientManager,
    MCPServerConfig,
    collect_required_servers,
    parse_mcp_servers,
)
from swarmkit_runtime.model_providers import (
    MockModelProvider,
    ProviderRegistry,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

if TYPE_CHECKING:
    from swarmkit_runtime._observability import Observability
from swarmkit_runtime.resolver import ResolvedWorkspace, resolve_workspace
from swarmkit_runtime.skills import impl_get


def _get_field(obj: Any, name: str) -> str:
    """Extract a string field from a dict or pydantic/dataclass object."""
    if isinstance(obj, dict):
        return obj.get(name, "") or ""
    return getattr(obj, name, "") or ""


@dataclass(frozen=True)
class RunEvent:
    """A single event from a topology execution."""

    event_type: str
    agent_id: str
    timestamp: str
    payload: dict[str, object] = field(default_factory=dict)
    skill_id: str | None = None


@dataclass(frozen=True)
class UsageSummary:
    """Token usage from a topology execution."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    by_agent: dict[str, dict[str, int]] = field(default_factory=dict)
    # per-model dict carries int token counts plus a float "cost" (USD).
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    # Read-side context compression savings (characters). Zero when off.
    compression_bytes_in: int = 0
    compression_bytes_out: int = 0
    compression_calls: int = 0


@dataclass(frozen=True)
class RunResult:
    """Output of a topology execution."""

    output: str
    agent_results: dict[str, str] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)
    usage: UsageSummary | None = None
    trace_data: dict[str, Any] | None = None


def _run_trace_to_span(trace: Any, workspace_id: str) -> Any:
    """Convert a finished ``RunTrace`` into an OTel ``RecordedSpan`` tree (design:
    runtime/otel-trace-export): topology.run → agent.step.<id> → tool.call.<name>. Tool calls have
    only a duration, so they're laid out sequentially inside their agent step's window."""
    from swarmkit_runtime.telemetry import RecordedSpan  # noqa: PLC0415

    def _ns(seconds: float) -> int:
        return int(seconds * 1_000_000_000)

    steps = []
    for step in trace.agent_steps:
        step_start = step.start_time or trace.start_time
        step_end = step.end_time or step_start
        cursor = step_start
        tools = []
        for tc in step.tool_calls:
            t_start, t_end = cursor, cursor + tc.duration_ms / 1000
            cursor = t_end
            tools.append(
                RecordedSpan(
                    name=f"tool.call.{tc.tool_name}",
                    start_ns=_ns(t_start),
                    end_ns=_ns(t_end),
                    attributes={
                        "swarmkit.tool.name": tc.tool_name,
                        "swarmkit.tool.result_length": tc.result_length,
                        "swarmkit.tool.cached": tc.cached,
                    },
                    error=tc.error,
                )
            )
        steps.append(
            RecordedSpan(
                name=f"agent.step.{step.agent_id}",
                start_ns=_ns(step_start),
                end_ns=_ns(step_end),
                attributes={
                    "swarmkit.agent.id": step.agent_id,
                    "swarmkit.agent.role": step.role,
                    "swarmkit.model.id": step.model,
                    "swarmkit.model.tokens_in": step.input_tokens,
                    "swarmkit.model.tokens_out": step.output_tokens,
                    "swarmkit.model.cost_usd": step.cost_usd,
                    # executor.kind/ref so a model and a harness node query uniformly (§5).
                    "swarmkit.executor.kind": getattr(step, "executor_kind", "model"),
                    "swarmkit.executor.ref": getattr(step, "executor_ref", ""),
                },
                error=step.error,
                children=tuple(tools),
            )
        )
    return RecordedSpan(
        name="topology.run",
        start_ns=_ns(trace.start_time),
        end_ns=_ns(trace.end_time or trace.start_time),
        attributes={
            "swarmkit.run.id": trace.run_id,
            "swarmkit.topology.id": trace.topology,
            "swarmkit.workspace.id": workspace_id,
            "swarmkit.run.llm_calls": trace.llm_calls,
            "swarmkit.model.tokens_in": trace.total_input_tokens,
            "swarmkit.model.tokens_out": trace.total_output_tokens,
            "swarmkit.model.cost_usd": trace.total_cost_usd,
        },
        children=tuple(steps),
    )


def _finalize_trace(trace: Any, workspace_root: Path, workspace_id: str) -> None:
    """Persist a finished ``RunTrace`` to disk and mirror it to OTel (design:
    runtime/otel-trace-export) so Jaeger/Grafana show the run. The OTel export is best-effort —
    telemetry never fails a run."""
    trace.save(workspace_root)
    with contextlib.suppress(Exception):
        from swarmkit_runtime.telemetry import get_telemetry  # noqa: PLC0415

        telemetry = get_telemetry()
        if telemetry.enabled:
            telemetry.export_run_spans(_run_trace_to_span(trace, workspace_id))
            telemetry.export_run_metrics(trace)


class MissingMCPServerError(Exception):
    """A skill targets an MCP server that the workspace doesn't declare."""

    def __init__(self, missing: list[tuple[str, str]]) -> None:
        self.missing = missing
        lines = [
            f"skill '{sid}' targets MCP server '{srv}' but the workspace declares no such server"
            for sid, srv in missing
        ]
        super().__init__("\n".join(lines))


class WorkspaceRuntime:
    """The backend that both CLI and HTTP server call into.

    Holds a resolved workspace plus all the wired runtime components
    (model providers, governance, MCP manager). Constructed via the
    ``from_workspace_path`` classmethod.
    """

    def __init__(
        self,
        *,
        workspace: ResolvedWorkspace,
        workspace_root: Path,
        provider_registry: ProviderRegistry,
        governance: GovernanceProvider,
        mcp_manager: MCPClientManager | None,
        audit_provider: AuditProvider | None = None,
    ) -> None:
        self._workspace = workspace
        self._workspace_root = workspace_root
        self._provider_registry = provider_registry
        self._governance = governance
        self._mcp_manager = mcp_manager
        self._memory_store = self._create_memory_store()
        self._audit_provider = audit_provider or audit_provider_for_path(workspace_root)
        self._session_active = False

    def _create_memory_store(self) -> Any:
        """Create a memory store for the workspace if memory bindings are configured.

        Returns GBrainMemory if a GBrain MCP server is configured,
        otherwise falls back to MemoryStore (local JSON + TF-IDF).
        """
        ws_gov = getattr(self._workspace.raw, "governance", None)
        if ws_gov is None:
            return None
        ds_list = getattr(ws_gov, "decision_skills", None) or []
        has_memory = any(
            getattr(ds, "id", "") in ("memory-reader", "memory-writer") for ds in ds_list
        )
        if not has_memory:
            return None

        if self._mcp_manager is not None:
            mcp_servers = getattr(self._workspace.raw, "mcp_servers", None) or []
            has_gbrain = any(getattr(s, "id", "") == "gbrain" for s in mcp_servers)
            if has_gbrain:
                from swarmkit_runtime.memory import GBrainMemory  # noqa: PLC0415

                return GBrainMemory(self._mcp_manager)

        from swarmkit_runtime.memory import MemoryStore  # noqa: PLC0415

        return MemoryStore(self._workspace_root)

    @staticmethod
    def audit_provider_for(path: Path) -> SqlAuditProvider:
        """Get the audit provider for a workspace without loading the full runtime.

        Used by CLI commands (status, logs, notifications) that only need to query events — not
        compile or run topologies. Same backend (sqlite / postgres) + DB the full runtime uses.
        """
        return audit_provider_for_path(path)

    @staticmethod
    def observability(path: Path) -> Observability:
        """Read-only observability facade (audit store + JSONL run-log access) for a workspace.

        Used by the observability CLI commands (logs/status/why/ask/…) that only need to read a
        workspace's run history — not compile or run topologies.
        """
        from swarmkit_runtime._observability import Observability  # noqa: PLC0415

        return Observability(path)

    @classmethod
    def from_workspace_path(cls, path: Path) -> WorkspaceRuntime:
        """Build a fully-wired runtime from a workspace directory.

        Resolves the workspace, registers model providers, selects the
        governance provider, parses MCP server config, and validates
        that every mcp_tool skill targets a configured server.

        Raises ``ResolutionErrors`` if the workspace is invalid, or
        ``MissingMCPServerError`` if skills reference unconfigured
        MCP servers.
        """
        ws_root = path.resolve()
        workspace = resolve_workspace(ws_root)

        registry = ProviderRegistry()
        register_available_providers(registry)

        governance = build_governance(workspace, ws_root)

        mcp_configs = parse_mcp_servers(getattr(workspace.raw, "mcp_servers", None))
        mcp_manager = MCPClientManager(mcp_configs, workspace_root=ws_root) if mcp_configs else None

        missing = find_missing_mcp_servers(workspace, mcp_configs)

        decision_skills = {
            sid: skill
            for sid, skill in workspace.skills.items()
            if getattr(skill.raw, "category", None) == "decision"
        }
        if decision_skills:
            import os  # noqa: PLC0415

            from swarmkit_runtime.governance._skill_backed import (  # noqa: PLC0415
                SkillBackedGovernanceProvider,
            )

            _model = os.environ.get("SWARMKIT_JUDGE_MODEL", "")
            _default_provider = registry.get("openrouter") or registry.get("default")
            governance = SkillBackedGovernanceProvider(
                base=governance,
                skills=decision_skills,
                model_provider=_default_provider or governance,  # type: ignore[arg-type]
                model_name=_model,
                mcp_manager=mcp_manager,
            )
        if missing:
            raise MissingMCPServerError(missing)

        return cls(
            workspace=workspace,
            workspace_root=ws_root,
            provider_registry=registry,
            governance=governance,
            mcp_manager=mcp_manager,
        )

    def _get_checkpointer(self) -> Any:
        """Get or create the checkpointer for run state persistence."""
        if not hasattr(self, "_checkpointer"):
            try:
                from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415

                db_path = self._workspace_root / ".swarmkit" / "state" / "checkpoints.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                self._checkpointer = SqliteSaver.from_conn_string(str(db_path))
            except ImportError:
                from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

                self._checkpointer = MemorySaver()
        return self._checkpointer

    def compile(self, topology_name: str) -> CompiledStateGraph[Any]:
        """Compile a named topology into a LangGraph graph.

        Raises ``KeyError`` if the topology doesn't exist.
        """
        if topology_name not in self._workspace.topologies:
            available = sorted(self._workspace.topologies.keys())
            raise KeyError(
                f"Topology '{topology_name}' not found. "
                f"Available: {', '.join(available) or '(none)'}."
            )

        topology = self._workspace.topologies[topology_name]
        decision_bindings = self._resolve_decision_bindings(topology_name)
        planning = self._resolve_planning_config(topology_name)
        synthesis = self._resolve_synthesis_config(topology_name)
        return compile_topology(
            topology,
            provider_registry=self._provider_registry,
            governance=self._governance,
            mcp_manager=self._mcp_manager,
            checkpointer=self._get_checkpointer(),
            workspace_root=self._workspace_root,
            decision_skill_bindings=decision_bindings,
            planning_config=planning,
            synthesis_config=synthesis,
            memory_store=self._memory_store,
        )

    def _resolve_planning_config(self, topology_name: str) -> Any:
        """Resolve planning config from workspace + topology (topology wins)."""
        from swarmkit_runtime.langgraph_compiler._state import (  # noqa: PLC0415
            DEFAULT_SYNTHESIS_ROLES,
            DEFAULT_SYNTHESIZER_ROLE,
            PlanningConfig,
        )

        ws_planning = getattr(self._workspace.raw, "planning", None)
        topo = self._workspace.topologies.get(topology_name)
        topo_runtime = getattr(topo.raw, "runtime", None) if topo else None
        topo_planning = getattr(topo_runtime, "planning", None) if topo_runtime else None

        scope_required = False
        two_phase = False
        synthesis_roles: tuple[str, ...] = DEFAULT_SYNTHESIS_ROLES
        synthesizer_role = DEFAULT_SYNTHESIZER_ROLE

        for src in (ws_planning, topo_planning):  # topology overrides workspace
            if not src:
                continue
            sr = getattr(src, "scope_required", None)
            tp = getattr(src, "two_phase", None)
            roles = getattr(src, "synthesis_roles", None)
            synth = getattr(src, "synthesizer_role", None)
            if sr is not None:
                scope_required = sr
            if tp is not None:
                two_phase = tp
            if roles:
                # 'self' is structural (inline coordinator execution) — always a synthesis role,
                # deduped, even if a user's list omits it.
                synthesis_roles = tuple(dict.fromkeys(("self", *roles)))
            if synth:
                synthesizer_role = synth

        return PlanningConfig(
            scope_required=scope_required,
            two_phase=two_phase,
            synthesis_roles=synthesis_roles,
            synthesizer_role=synthesizer_role,
        )

    def _resolve_synthesis_config(self, topology_name: str) -> Any:
        """Resolve synthesis config from workspace + topology (topology wins)."""
        from swarmkit_runtime.langgraph_compiler._state import SynthesisConfig  # noqa: PLC0415

        provider = ""
        model = ""
        prompt = ""

        ws_raw = getattr(self._workspace.raw, "synthesis", None)
        if ws_raw is not None:
            provider = _get_field(ws_raw, "provider")
            model = _get_field(ws_raw, "model")
            prompt = _get_field(ws_raw, "prompt")

        topo = self._workspace.topologies.get(topology_name)
        topo_runtime = getattr(topo.raw, "runtime", None) if topo else None
        topo_synth = getattr(topo_runtime, "synthesis", None) if topo_runtime else None
        if topo_synth is not None:
            tp = _get_field(topo_synth, "provider")
            tm = _get_field(topo_synth, "model")
            tpr = _get_field(topo_synth, "prompt")
            if tp:
                provider = tp
            if tm:
                model = tm
            if tpr:
                prompt = tpr

        if not model:
            return None

        return SynthesisConfig(provider=provider, model=model, prompt=prompt)

    def _resolve_decision_bindings(self, topology_name: str) -> list[DecisionSkillBinding]:
        """Merge workspace + topology decision skill bindings."""
        ws_raw = getattr(self._workspace.raw, "governance", None)
        ws_skills: list[dict[str, Any]] = []
        if ws_raw:
            ws_skills = getattr(ws_raw, "decision_skills", None) or []
            if hasattr(ws_skills, "root"):
                ws_skills = ws_skills.root if ws_skills.root else []

        topo = self._workspace.topologies.get(topology_name)
        topo_skills: list[dict[str, Any]] = []
        if topo:
            topo_gov = getattr(topo.raw, "governance", None)
            if topo_gov:
                topo_skills = getattr(topo_gov, "decision_skills", None) or []
                if hasattr(topo_skills, "root"):
                    topo_skills = topo_skills.root if topo_skills.root else []

        if not ws_skills and not topo_skills:
            return []

        ws_dicts = [s if isinstance(s, dict) else s.model_dump() for s in ws_skills]
        topo_dicts = [s if isinstance(s, dict) else s.model_dump() for s in topo_skills]
        return merge_decision_skills(ws_dicts, topo_dicts)

    async def start_session(self) -> None:
        """Start MCP servers and keep them alive for multiple runs.

        Use this for chat/conversation mode where MCP servers should
        persist across turns. Call end_session() when done.
        """
        if self._mcp_manager is not None and not self._session_active:
            await self._mcp_manager.start_all()
            self._session_active = True

    async def end_session(self) -> None:
        """Stop MCP servers started by start_session()."""
        if self._mcp_manager is not None and self._session_active:
            await self._mcp_manager.close_all()
            self._session_active = False

    async def run(
        self,
        topology_name: str,
        user_input: str,
        *,
        max_steps: int = 50,
        thread_id: str | None = None,
        previous_plan: dict | None = None,  # type: ignore[type-arg]
    ) -> RunResult:
        """Execute a topology end-to-end and return the result.

        If a session is active (via start_session), MCP servers are
        already running and won't be restarted. Otherwise, handles
        MCP lifecycle per-run (start_all / close_all).

        Pass ``thread_id`` to enable checkpoint-based resume. The same
        thread_id is used to resume a deferred run later.
        Pass ``previous_plan`` to seed the run with a task plan from
        a previous crashed run.
        """
        from uuid import uuid4  # noqa: PLC0415

        from swarmkit_runtime.compression import (  # noqa: PLC0415
            build_policy,
            set_active_policy,
        )
        from swarmkit_runtime.langgraph_compiler._compiler import set_active_trace  # noqa: PLC0415
        from swarmkit_runtime.langgraph_compiler._run_context import run_context  # noqa: PLC0415
        from swarmkit_runtime.trace import RunTrace  # noqa: PLC0415

        graph = self.compile(topology_name)
        topology = self._workspace.topologies[topology_name]
        run_thread = thread_id or str(uuid4())

        trace = RunTrace()
        trace.start(run_thread, topology_name)
        set_active_trace(trace)
        # Opt-in read-side context compression for this run. Resolved from the workspace
        # `context_compression:` block (default backend + per-surface overrides), with env
        # vars overriding the default per deployment. Off unless configured.
        compression_cfg = getattr(self._workspace.raw, "context_compression", None)
        set_active_policy(build_policy(compression_cfg))

        effective_limit = max(max_steps, _compute_recursion_limit(topology))

        owns_mcp = not self._session_active
        if owns_mcp and self._mcp_manager is not None:
            required = collect_required_servers(topology)
            await self._mcp_manager.start_required(required)
        try:
            initial_task_plan: dict = previous_plan if previous_plan else {}  # type: ignore[type-arg]
            initial_agent_results: dict = {}  # type: ignore[type-arg]
            initial_delegation_counts: dict = {}  # type: ignore[type-arg]
            if previous_plan:
                from swarmkit_runtime.langgraph_compiler._sentinels import (  # noqa: PLC0415
                    AgentStatus,
                    TaskStatus,
                    make_delegated,
                )

                leader_ids = [c.id for c in topology.root.children]
                if leader_ids:
                    leader_id = leader_ids[0]
                    tasks = previous_plan.get("tasks", [])
                    all_done = all(
                        t.get("status") in (TaskStatus.COMPLETED, TaskStatus.FAILED) for t in tasks
                    )
                    if all_done:
                        initial_agent_results[leader_id] = AgentStatus.TASK_PLAN_COMPLETE
                    else:
                        initial_agent_results[leader_id] = AgentStatus.TASK_PLAN_EXECUTING
                    initial_agent_results[topology.root.id] = make_delegated(leader_id)
                    initial_delegation_counts[leader_id] = 1

            # Scope run-state (tasks.json / scope.json / results) to this run's own
            # directory so concurrent runs in one process don't corrupt each other.
            with run_context(self._workspace_root, run_thread):
                result = await graph.ainvoke(
                    {
                        "input": user_input,
                        "messages": [],
                        "agent_results": initial_agent_results,
                        "delegation_counts": initial_delegation_counts,
                        "task_plan": initial_task_plan,
                        "current_agent": "",
                        "output": "",
                    },
                    config={
                        "recursion_limit": effective_limit,
                        "configurable": {"thread_id": run_thread},
                    },
                )
        finally:
            set_active_policy(None)
            if owns_mcp and self._mcp_manager is not None:
                await self._mcp_manager.close_all()

        trace.finish()
        set_active_trace(None)
        _finalize_trace(trace, self._workspace_root, self.workspace_id)

        events = _extract_events(self._governance)

        await self._persist_events_to_audit(events, topology_name)

        usage = UsageSummary(
            input_tokens=trace.total_input_tokens,
            output_tokens=trace.total_output_tokens,
            total_tokens=trace.total_input_tokens + trace.total_output_tokens,
            cost_usd=trace.total_cost_usd,
            by_agent=dict(trace.token_by_agent),
            by_model=dict(trace.token_by_model),
            compression_bytes_in=trace.compression_bytes_in,
            compression_bytes_out=trace.compression_bytes_out,
            compression_calls=trace.compression_calls,
        )

        trace_summary = {
            "run_id": trace.run_id,
            "duration_ms": trace.duration_ms,
            "llm_calls": trace.llm_calls,
            "agent_steps": [
                {
                    "agent_id": s.agent_id,
                    "model": s.model,
                    "duration_ms": s.duration_ms,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "tool_calls": [
                        {
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "result_length": tc.result_length,
                            "duration_ms": tc.duration_ms,
                            "error": tc.error,
                        }
                        for tc in s.tool_calls
                    ],
                }
                for s in trace.agent_steps
            ],
        }

        return RunResult(
            output=result.get("output", ""),
            agent_results={
                k: str(v) for k, v in result.get("agent_results", {}).items() if isinstance(v, str)
            },
            events=events,
            usage=usage,
            trace_data=trace_summary,
        )

    async def resume(
        self,
        topology_name: str,
        thread_id: str,
        *,
        max_steps: int = 50,
    ) -> RunResult:
        """Resume a previously checkpointed run.

        Rehydrates graph state from the SQLite checkpoint and continues
        execution from where it was interrupted (e.g., after HITL defer).
        """
        graph = self.compile(topology_name)
        topology = self._workspace.topologies[topology_name]

        effective_limit = max(max_steps, _compute_recursion_limit(topology))

        owns_mcp = not self._session_active
        if owns_mcp and self._mcp_manager is not None:
            required = collect_required_servers(topology)
            await self._mcp_manager.start_required(required)
        try:
            result = await graph.ainvoke(
                None,
                config={
                    "recursion_limit": effective_limit,
                    "configurable": {"thread_id": thread_id},
                },
            )
        finally:
            if owns_mcp and self._mcp_manager is not None:
                await self._mcp_manager.close_all()

        events = _extract_events(self._governance)
        await self._persist_events_to_audit(events, topology_name)

        return RunResult(
            output=result.get("output", "") if result else "",
            agent_results={
                k: str(v)
                for k, v in (result or {}).get("agent_results", {}).items()
                if isinstance(v, str)
            },
            events=events,
        )

    async def judge(
        self,
        *,
        skill_id: str,
        content: str,
        trigger: str = "eval",
    ) -> DecisionSkillResult:
        """Score ``content`` against a decision skill, reusing the wired judge.

        Delegates to ``GovernanceProvider.evaluate_decision_skill`` — the
        SkillBackedGovernanceProvider already has a resolved model provider/model, so
        no extra plumbing. Used by the eval harness's LLM rubric tier. Returns a
        default "pass" if no decision-skill evaluator is configured.
        """
        return await self._governance.evaluate_decision_skill(
            skill_id=skill_id,
            trigger=trigger,
            agent_id="eval-runner",
            content=content,
        )

    async def judge_rubric(
        self,
        *,
        rubric: str,
        content: str,
        trigger: str = "eval",
    ) -> DecisionSkillResult:
        """Score ``content`` against an inline rubric (no decision skill needed).

        Used by the eval harness's inline-``rubric:`` tier. Resolves a model provider
        the same way authoring does, asks for the decision-skill verdict shape, and
        reuses the decision-skill parser — so the result is identical to ``judge``.
        """
        from swarmkit_runtime.governance._decision_evaluator import _parse_result  # noqa: PLC0415
        from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

        provider, model = resolve_authoring_provider(self._provider_registry)
        system = (
            "You are an evaluation judge. Decide whether the CONTENT satisfies the "
            'RUBRIC. Respond with ONLY a JSON object: {"verdict": "pass" or "fail", '
            '"confidence": a number from 0.0 to 1.0, "reasoning": "one sentence"}.\n\n'
            f"RUBRIC:\n{rubric}"
        )
        user = f"Trigger: {trigger}\n\n--- CONTENT TO EVALUATE ---\n{content}\n--- END CONTENT ---"
        response = await provider.complete(
            CompletionRequest(
                model=model,
                messages=(Message(role="user", content=user),),
                system=system,
            )
        )
        text = (
            "\n".join(b.text for b in response.content if b.type == "text" and b.text)
            or "(no response)"
        )
        return _parse_result("inline-rubric", text)

    async def _persist_events_to_audit(self, events: list[RunEvent], topology_name: str) -> None:
        """Write extracted events to the AuditProvider with redaction applied."""
        from datetime import UTC, datetime  # noqa: PLC0415

        from swarmkit_runtime.audit import apply_audit_policy, resolve_audit_config  # noqa: PLC0415
        from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

        ws_audit_level = self._get_workspace_audit_level()

        for evt in events:
            try:
                ts = (
                    datetime.fromisoformat(evt.timestamp) if evt.timestamp else datetime.now(tz=UTC)
                )
            except (ValueError, TypeError):
                ts = datetime.now(tz=UTC)
            raw_duration = evt.payload.get("duration_ms")
            duration: int | None = int(raw_duration) if raw_duration is not None else None  # type: ignore[call-overload]
            raw_role = evt.payload.get("role")
            role = str(raw_role) if raw_role is not None else None

            redacted = self._apply_skill_redaction(
                evt, ws_audit_level, resolve_audit_config, apply_audit_policy
            )

            audit_event = AuditEvent(
                event_type=evt.event_type,
                agent_id=evt.agent_id,
                timestamp=ts,
                skill_id=evt.skill_id,
                topology_id=topology_name,
                payload=redacted,
                duration_ms=duration,
                agent_role=role,  # type: ignore[arg-type]
            )
            await self._audit_provider.record(audit_event)

    def _get_workspace_audit_level(self) -> str | None:
        """Read workspace-level audit.level from storage config."""
        storage = getattr(self._workspace.raw, "storage", None)
        if storage is None:
            return None
        audit_cfg = getattr(storage, "audit", None)
        if audit_cfg is None:
            return None
        return getattr(audit_cfg, "level", None)

    def _apply_skill_redaction(
        self,
        evt: RunEvent,
        ws_level: str | None,
        resolve_fn: Any,
        apply_fn: Any,
    ) -> dict[str, object]:
        """Apply per-skill audit redaction to event payload."""
        if not evt.skill_id or evt.skill_id not in self._workspace.skills:
            return dict(evt.payload)

        skill = self._workspace.skills[evt.skill_id]
        skill_audit = getattr(skill.raw, "audit", None)
        skill_category = getattr(skill.raw, "category", None)

        log_inputs, log_outputs, redact_paths = resolve_fn(
            skill_audit, skill_category, workspace_level=ws_level
        )

        payload = dict(evt.payload)

        if "inputs" in payload and isinstance(payload["inputs"], dict):
            payload["inputs"] = apply_fn(
                payload["inputs"],
                field="inputs",
                log_level=log_inputs,
                redact_paths=redact_paths,
            )

        if "outputs" in payload and isinstance(payload["outputs"], dict):
            payload["outputs"] = apply_fn(
                payload["outputs"],
                field="outputs",
                log_level=log_outputs,
                redact_paths=redact_paths,
            )

        return payload

    async def close(self) -> None:
        """Release all held resources."""
        await self.end_session()
        if self._mcp_manager is not None:
            await self._mcp_manager.close_all()

    @property
    def workspace(self) -> ResolvedWorkspace:
        return self._workspace

    @property
    def workspace_id(self) -> str:
        """The workspace's declared id (``metadata.id``) — identifies this instance in telemetry
        (``swarmkit.workspace.id``). Falls back to the directory name."""
        meta = getattr(self._workspace.raw, "metadata", None)
        return str(getattr(meta, "id", "") or self._workspace_root.name)

    @property
    def workspace_name(self) -> str:
        """The workspace's human-readable name (``metadata.name``); empty if unset."""
        meta = getattr(self._workspace.raw, "metadata", None)
        return str(getattr(meta, "name", "") or "")

    @property
    def telemetry_service_name(self) -> str:
        """OTel ``service.name`` — ``"<name> (<id>)"`` when a name is set, else the id — so a fleet
        is legible in Jaeger's service list (design: runtime/otel-trace-export)."""
        name, wid = self.workspace_name, self.workspace_id
        return f"{name} ({wid})" if name else wid

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def governance(self) -> GovernanceProvider:
        return self._governance

    @property
    def audit_provider(self) -> AuditProvider:
        return self._audit_provider

    @property
    def mcp_manager(self) -> MCPClientManager | None:
        return self._mcp_manager

    @property
    def provider_registry(self) -> ProviderRegistry:
        return self._provider_registry


# ---- event extraction ----------------------------------------------------


def _extract_events(governance: GovernanceProvider) -> list[RunEvent]:
    """Pull audit events from the governance provider after a run.

    Works with MockGovernanceProvider (has .events property) and
    AGTGovernanceProvider (has .get_log() on the FlightRecorder).
    Returns empty list for providers that don't expose events.
    """
    raw_events = getattr(governance, "events", None)
    if raw_events is None:
        recorder = getattr(governance, "_recorder", None)
        if recorder is not None and hasattr(recorder, "get_log"):
            raw_events = recorder.get_log()
    if not raw_events:
        return []

    result: list[RunEvent] = []
    for evt in raw_events:
        if hasattr(evt, "event_type"):
            result.append(
                RunEvent(
                    event_type=evt.event_type,
                    agent_id=evt.agent_id,
                    timestamp=str(evt.timestamp),
                    payload=dict(evt.payload) if evt.payload else {},
                    skill_id=evt.skill_id,
                )
            )
        elif isinstance(evt, dict):
            result.append(
                RunEvent(
                    event_type=evt.get("event_type", "unknown"),
                    agent_id=evt.get("agent_id", ""),
                    timestamp=str(evt.get("timestamp", "")),
                    payload={
                        k: v
                        for k, v in evt.items()
                        if k not in {"event_type", "agent_id", "timestamp"}
                    },
                )
            )
    return result


# ---- helpers (public — used by CLI and tests) ----------------------------


def register_available_providers(registry: ProviderRegistry) -> None:
    """Register all model providers whose credentials are in the environment.

    Providers whose SDK dependencies are missing are silently skipped.
    """
    registry.register(MockModelProvider())

    _conditional = [
        ("ANTHROPIC_API_KEY", "AnthropicModelProvider"),
        ("GOOGLE_API_KEY", "GoogleModelProvider"),
        ("OPENAI_API_KEY", "OpenAIModelProvider"),
        ("OPENROUTER_API_KEY", "OpenRouterModelProvider"),
        ("GROQ_API_KEY", "GroqModelProvider"),
        ("TOGETHER_API_KEY", "TogetherModelProvider"),
    ]
    for env_var, cls_name in _conditional:
        if os.environ.get(env_var):
            try:
                from swarmkit_runtime import model_providers  # noqa: PLC0415

                cls = getattr(model_providers, cls_name)
                registry.register(cls())
            except (ImportError, ModuleNotFoundError, AttributeError):
                pass

    try:
        from swarmkit_runtime import model_providers  # noqa: PLC0415

        registry.register(model_providers.OllamaModelProvider())
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass


def build_governance(workspace: ResolvedWorkspace, ws_root: Path) -> GovernanceProvider:
    """Select the GovernanceProvider based on workspace.yaml's governance block."""
    gov = getattr(workspace.raw, "governance", None)
    if gov is None:
        return MockGovernanceProvider(allow_all=True)

    provider_value = gov.provider.value if hasattr(gov.provider, "value") else str(gov.provider)

    if provider_value == "agt":
        from swarmkit_runtime.governance.agt_provider import AGTGovernanceProvider  # noqa: PLC0415

        config = gov.config or {}
        policies_dir = ws_root / config.get("policies_dir", "policies")
        audit_db = ws_root / ".swarmkit" / "audit.db"
        audit_db.parent.mkdir(parents=True, exist_ok=True)
        return AGTGovernanceProvider.from_config(
            policy_dir=policies_dir,
            audit_db=audit_db,
        )

    if provider_value == "custom":
        # Fail closed: silently downgrading an explicitly-requested governance provider to
        # allow-all defeats the point of asking for it. A workspace that sets provider=custom
        # wants real governance — surface the gap instead of running ungoverned.
        raise ValueError(
            "governance.provider=custom is not yet supported (no plugin path wired). "
            "Use provider: agt (or omit governance for the mock allow-all default). "
            "See design §8.5."
        )

    return MockGovernanceProvider(allow_all=True)


def resolve_authoring_provider(
    registry: ProviderRegistry | None = None,
) -> tuple[ModelProviderProtocol, str]:
    """Resolve which model provider + model name to use for authoring.

    Checks SWARMKIT_AUTHOR_MODEL (format: provider/model), then falls
    back to SWARMKIT_PROVIDER + SWARMKIT_MODEL, then first available
    real provider.
    """
    author_model = os.environ.get("SWARMKIT_AUTHOR_MODEL", "")
    if "/" in author_model:
        provider_id, model_name = author_model.split("/", 1)
    else:
        provider_id = os.environ.get("SWARMKIT_PROVIDER", "")
        model_name = os.environ.get("SWARMKIT_MODEL", "")

    if registry is None:
        registry = ProviderRegistry()
        register_available_providers(registry)

    if provider_id:
        provider = registry.get(provider_id)
        if provider is not None:
            return provider, model_name or "claude-sonnet-4-6"

    _preferred = ["openrouter", "anthropic", "openai", "google", "groq", "together"]
    for pid in _preferred:
        provider = registry.get(pid)
        if provider is not None:
            default_model = "deepseek/deepseek-chat" if pid == "openrouter" else "claude-sonnet-4-6"
            return provider, model_name or default_model

    for pid in registry.provider_ids:
        if pid == "mock":
            continue
        provider = registry.get(pid)
        if provider is not None:
            return provider, model_name or "deepseek/deepseek-chat"

    raise RuntimeError(
        "No model provider available. Set SWARMKIT_PROVIDER "
        "and the corresponding API key (e.g. GROQ_API_KEY)."
    )


def find_missing_mcp_servers(
    workspace: ResolvedWorkspace,
    mcp_configs: dict[str, MCPServerConfig],
) -> list[tuple[str, str]]:
    """Return ``(skill_id, server_id)`` pairs whose mcp_tool target is unconfigured."""
    missing: list[tuple[str, str]] = []
    for skill_id, skill in workspace.skills.items():
        impl = skill.raw.implementation
        if impl_get(impl, "type") != "mcp_tool":
            continue
        server_id = str(impl_get(impl, "server"))
        if server_id and server_id not in mcp_configs:
            missing.append((skill_id, server_id))
    return missing


def _compute_recursion_limit(topology: Any) -> int:
    """Compute a safe recursion limit based on topology size.

    Each agent delegation is a graph step. Multi-level topologies
    (root → coordinator → sub-agents) need more steps than flat ones.
    Formula: 10 steps per agent, minimum 50.
    """
    from swarmkit_runtime.resolver import ResolvedAgent  # noqa: PLC0415

    def _count_agents(agent: ResolvedAgent) -> int:
        return 1 + sum(_count_agents(c) for c in agent.children)

    count = _count_agents(topology.root)
    return max(50, count * 10)
