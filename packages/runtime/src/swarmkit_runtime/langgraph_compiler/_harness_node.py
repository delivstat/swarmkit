"""Harness-executor node execution (executor-abstraction §5-§6.2, P2 PR6).

`_build_agent_node` runs the model tool-loop for `executor.kind == "model"` (byte-identical to
before the executor seam) and, for any other kind, hands off here — the harness runner:

    preflight → provision worktree sandbox → inject TaskSpec → run() streaming ExecEvents
    → enforce budget/idle → collect the diff artifact → teardown → hand back to the graph

Interaction is **deny / abort only** in P2 (§6.2): a request outside the launch grant (an approval
or an input question) never relays to a human and never hangs — it terminates the run with
``exec.result{status: needs_approval}``. The budget guard's idle timeout is the never-hang backstop.

Checkpointing in P2 = audit events: ``executor.started`` before launch and ``executor.result`` at
the terminal event. The richer OTel/cost projection of the *inner* ExecEvents is P2 PR7.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    ExecApprovalRequested,
    ExecInputRequested,
    ExecResult,
    ExecStarted,
    ExecToolCall,
    ExecUsage,
    Executor,
    ExecutorError,
    InteractionDriver,
    NoInteractionDriver,
    ResolvedExecutor,
    SandboxError,
    SandboxHandle,
    TaskSpec,
    collect_diff,
    enforce_budget,
    is_launch_approved,
    load_adapter_specs,
    load_workspace_adapter_specs,
    worktree_sandbox,
)
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.model_providers._pricing import estimate_cost
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.trace import AgentStep, ToolCall

from ._helpers import _make_result
from ._relay import resolve_relay
from ._run_context import current_parent_agent

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.langgraph_compiler._state import SwarmState
    from swarmkit_runtime.resolver import ResolvedAgent
    from swarmkit_runtime.review import ReviewQueue

# Default idle timeout (seconds): the never-hang backstop when a harness config sets none.
_DEFAULT_IDLE_SECONDS = 120.0


def _build_executor(resolved: ResolvedExecutor, workspace_root: Path | None) -> Executor:
    """Construct a runnable executor for a resolved block by looking its ``kind`` up among the
    declarative adapters (bundled + the workspace's ``adapters/``). ``model`` never reaches here —
    the compiler runs it via its own node.

    Every harness is a declarative ``adapter.yaml`` (bundled or workspace-local) — no harness is
    special-cased in code. An otherwise-unknown kind raises.
    """
    specs = load_adapter_specs(workspace_root)
    spec = specs.get(resolved.kind)
    if spec is None:
        raise ExecutorError(
            f"no adapter for executor kind {resolved.kind!r} (known adapters: {sorted(specs)})"
        )
    # Launch-block review gate (§5.2): a workspace-authored adapter's launch surface — a command
    # line run on the host — must be human-approved (and re-approved on change). Bundled reference
    # adapters are pre-vetted, so they bypass. This is a human-only scope: approval is a CLI action
    # (`swarmkit adapters approve`), never something an agent can grant.
    if workspace_root is not None and resolved.kind in load_workspace_adapter_specs(workspace_root):  # noqa: SIM102
        if not is_launch_approved(workspace_root, spec):
            raise ExecutorError(
                f"adapter {resolved.kind!r} launch block is not approved — a workspace adapter's "
                f"launch command must be human-reviewed before it can run. Approve it with "
                f"`swarmkit adapters approve {resolved.kind}` after inspecting its launch."
            )
    credential = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CODEX_API_KEY")
    return DeclarativeExecutor(spec, config=resolved.config, model_provider_credential=credential)


def _budget_from_config(config: dict[str, Any]) -> BudgetEnvelope:
    budget = config.get("budget") or {}
    return BudgetEnvelope(
        max_cost_usd=budget.get("max_cost_usd"),
        max_turns=budget.get("max_turns"),
        max_wall_clock_minutes=budget.get("max_wall_clock_minutes"),
        max_idle_seconds=budget.get("max_idle_seconds", _DEFAULT_IDLE_SECONDS),
    )


def _task_spec(agent: ResolvedAgent, state: SwarmState, workspace_root: Path | None) -> TaskSpec:
    """Assemble the checkpointed task (§6.0): the statement, workspace CLAUDE.md as a context file,
    the agent's declared skills as tool grants, and the base ref."""
    statement = state.get("input", "") or agent.role
    context_files: dict[str, str] = {}
    if workspace_root is not None:
        claude_md = workspace_root / "CLAUDE.md"
        if claude_md.is_file():
            context_files["CLAUDE.md"] = claude_md.read_text()
    mcp_tools = tuple(sid for sid in (getattr(s, "id", "") for s in agent.skills) if sid)
    return TaskSpec(
        statement=statement,
        context_files=context_files,
        mcp_tools=mcp_tools,
        base_ref="HEAD",
    )


async def _record(
    governance: GovernanceProvider, event_type: str, agent_id: str, payload: dict[str, Any]
) -> None:
    await governance.record_event(
        AuditEvent(
            event_type=event_type,
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload=payload,
        )
    )


@asynccontextmanager
async def _persistent_dir(path: Path) -> AsyncIterator[SandboxHandle]:
    """A non-isolated, persistent working directory. Unlike the worktree it is NOT torn down and NOT
    reset to a base ref — the harness edits it in place, so a session-scoped harness (e.g. Claude
    Code, whose project memory is keyed to cwd) accumulates state across runs. The isolation of the
    worktree is traded for persistence; the diff it produces is informational, not an isolation
    boundary."""
    path.mkdir(parents=True, exist_ok=True)
    yield SandboxHandle(root=path, kind="directory", network="deny")


@dataclass(frozen=True)
class _RelayCtx:
    """Everything the relay path needs, bundled. ``on_unanswerable``/``driver``/``max_wait`` come
    from the adapter's ``interaction`` block; a harness with no relay driver leaves this at defaults
    and an out-of-grant request aborts (unchanged)."""

    on_unanswerable: str = "abort"
    driver: InteractionDriver = field(default_factory=NoInteractionDriver)
    review_queue: ReviewQueue | None = None
    max_wait_seconds: float | None = None
    topology_id: str = ""


def _relay_ctx(
    agent: ResolvedAgent,
    root: Path | None,
    driver: InteractionDriver | None,
    review_queue: ReviewQueue | None,
) -> _RelayCtx:
    """Assemble the relay context from the adapter spec + injected (test) overrides."""
    on_unanswerable = "abort"
    max_wait: float | None = None
    if root is not None:
        spec = load_adapter_specs(root).get(agent.executor.kind)
        if spec is not None:
            on_unanswerable = spec.on_unanswerable
            max_wait = spec.max_approval_wait_seconds
    return _RelayCtx(
        on_unanswerable=on_unanswerable,
        driver=driver if driver is not None else NoInteractionDriver(),
        review_queue=review_queue
        if review_queue is not None
        else (FileReviewQueue(root) if root else None),
        max_wait_seconds=max_wait,
    )


def _sandbox_for(agent: ResolvedAgent, root: Path, base_ref: str) -> tuple[Any, bool]:
    """Choose the harness's execution sandbox. With ``executor.config.working_dir`` set, run in that
    persistent directory (resolved under the workspace root); otherwise provision an isolated,
    ephemeral git worktree (the default). Returns ``(context_manager, persistent)``."""
    working_dir = agent.executor.config.get("working_dir")
    if working_dir:
        wd = Path(working_dir)
        resolved = wd if wd.is_absolute() else (root / wd)
        return _persistent_dir(resolved.resolve()), True
    return worktree_sandbox(root, base_ref), False


async def run_harness_node(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    *,
    workspace_root: Path | Any = None,
    executor: Executor | None = None,
    driver: InteractionDriver | None = None,
    review_queue: ReviewQueue | None = None,
) -> dict[str, Any]:
    """Execute an agent whose ``executor.kind`` is not ``model``.

    Returns the standard node result dict. Errors (preflight, sandbox, no result) become a failure
    result, never an exception — a harness node fails on an edge rather than crashing the graph.
    ``executor`` / ``driver`` / ``review_queue`` are injectable for testing; in production they are
    built from ``agent.executor`` + the workspace.
    """
    agent_id = agent.id
    kind = agent.executor.kind
    root = Path(workspace_root) if workspace_root is not None else None

    try:
        runner = executor if executor is not None else _build_executor(agent.executor, root)
    except ExecutorError as exc:
        await _record(governance, "executor.failed", agent_id, {"kind": kind, "reason": str(exc)})
        return _make_result(agent_id, f"[harness:{kind}] unavailable: {exc}")

    if root is None:
        reason = "no workspace root available to provision a harness sandbox"
        await _record(governance, "executor.failed", agent_id, {"kind": kind, "reason": reason})
        return _make_result(agent_id, f"[harness:{kind}] {reason}")

    budget = _budget_from_config(dict(agent.executor.config))
    task = _task_spec(agent, state, root)
    relay = _relay_ctx(agent, root, driver, review_queue)
    return await _execute(agent, state, governance, runner, task, budget, root, kind, relay)


class _Meter:
    """Accumulates the harness run's usage for the trace step (§5 observability parity)."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.vendor_cost = 0.0
        self.has_vendor_cost = False
        self.tool_calls: list[ToolCall] = []

    def add_usage(self, event: ExecUsage) -> None:
        self.input_tokens += event.input_tokens
        self.output_tokens += event.output_tokens
        self.cached_tokens += event.cached_tokens
        if event.cost_usd is not None:
            self.vendor_cost += event.cost_usd
            self.has_vendor_cost = True

    def cost(self, ref: str) -> float:
        """Vendor-reported cost is authoritative; else fall back to the price table by ``ref`` (the
        exact model/harness fallback model nodes already use). Unpriced ⇒ 0.0, never guessed."""
        if self.has_vendor_cost:
            return self.vendor_cost
        return estimate_cost(ref, self.input_tokens, self.output_tokens) if ref else 0.0


def _record_trace_step(
    agent: ResolvedAgent,
    kind: str,
    ref: str,
    start_ts: float,
    meter: _Meter,
    cost_usd: float,
    terminal: ExecResult | None,
    diff: str,
) -> None:
    """Append an :class:`AgentStep` to the active run trace so a harness node's tokens, cost, and
    tool calls roll into the run totals, the OTel span tree, and the metrics — just like a model
    node. No-op when there is no active trace (e.g. a direct unit-test call)."""
    from swarmkit_runtime.langgraph_compiler._compiler import get_active_trace  # noqa: PLC0415

    trace = get_active_trace()
    if trace is None:
        return
    end_ts = datetime.now(tz=UTC).timestamp()
    status = terminal.status if terminal is not None else "failure"
    output = terminal.output if terminal is not None else None
    result_length = len(diff) or (len(output) if isinstance(output, str) else 0)
    trace.add_step(
        AgentStep(
            agent_id=agent.id,
            model=ref or kind,
            parent_agent=current_parent_agent(),
            role=agent.role,
            start_time=start_ts,
            end_time=end_ts,
            duration_ms=int((end_ts - start_ts) * 1000),
            input_tokens=meter.input_tokens,
            output_tokens=meter.output_tokens,
            total_tokens=meter.input_tokens + meter.output_tokens,
            cost_usd=cost_usd,
            tool_calls=meter.tool_calls,
            result_length=result_length,
            error=None if status == "success" else status,
            executor_kind=kind,
            executor_ref=ref,
        )
    )


async def _execute(  # noqa: PLR0912, PLR0915
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    runner: Executor,
    task: TaskSpec,
    budget: BudgetEnvelope,
    root: Path,
    kind: str,
    relay: _RelayCtx,
) -> dict[str, Any]:
    agent_id = agent.id
    ref = agent.executor.ref or ""
    holder: dict[str, str | None] = {"run_id": None}
    meter = _Meter()
    start_ts = datetime.now(tz=UTC).timestamp()

    async def _cancel() -> None:
        run_id = holder["run_id"]
        if run_id is not None:
            await runner.cancel(run_id)

    sandbox_cm, persistent = _sandbox_for(agent, root, task.base_ref or "HEAD")
    try:
        async with sandbox_cm as sandbox:
            report = runner.preflight(task, sandbox)
            if not report.ok:
                await _record(
                    governance, "executor.failed", agent_id, {"kind": kind, "reason": report.reason}
                )
                return _make_result(agent_id, f"[harness:{kind}] preflight failed: {report.reason}")

            await _record(governance, "executor.started", agent_id, {"kind": kind})

            terminal: ExecResult | None = None
            guarded = enforce_budget(runner.run(task, sandbox, budget), budget, cancel=_cancel)
            async for event in guarded:
                if isinstance(event, ExecStarted):
                    holder["run_id"] = event.run_id
                elif isinstance(event, ExecUsage):
                    meter.add_usage(event)
                elif isinstance(event, ExecToolCall):
                    meter.tool_calls.append(
                        ToolCall(tool_name=event.tool, result_length=len(event.input_summary))
                    )
                elif isinstance(event, ExecApprovalRequested):
                    # relay (§6.2): pause, resolve via policy/inbox, feed the decision back.
                    # Requires a driver that supports bidirectional control; otherwise abort.
                    if (
                        relay.on_unanswerable == "relay"
                        and relay.driver.supports_relay
                        and relay.review_queue is not None
                    ):
                        decision = await resolve_relay(
                            event,
                            agent_id=agent_id,
                            topology_id=relay.topology_id,
                            governance=governance,
                            review_queue=relay.review_queue,
                            max_wait_seconds=relay.max_wait_seconds,
                        )
                        await relay.driver.respond(event, granted=decision.granted)
                        if decision.granted:
                            continue  # the harness proceeds; keep consuming the stream
                        await _cancel()
                        terminal = ExecResult(
                            status="needs_approval",
                            exit_metadata={
                                "denied": _interaction_summary(event),
                                "responder": decision.responder,
                            },
                        )
                        break
                    # deny / abort: no relay, no hang — terminate needs_approval.
                    await _cancel()
                    terminal = ExecResult(
                        status="needs_approval",
                        exit_metadata={"denied": _interaction_summary(event)},
                    )
                    break
                elif isinstance(event, ExecInputRequested):
                    # §6.3 input requests (judgment questions) are deferred — abort for now.
                    await _cancel()
                    terminal = ExecResult(
                        status="needs_approval",
                        exit_metadata={"denied": _interaction_summary(event)},
                    )
                    break
                elif isinstance(event, ExecResult):
                    terminal = event

            diff = ""
            if terminal is not None and terminal.status == "success":
                try:
                    diff = await collect_diff(sandbox)
                except SandboxError:
                    # A persistent working_dir need not be a git repo — the diff is informational
                    # there, not an isolation boundary, so a missing repo is not a failure.
                    if not persistent:
                        raise

            cost_usd = meter.cost(ref)
            _record_trace_step(agent, kind, ref, start_ts, meter, cost_usd, terminal, diff)
            return await _finish(governance, agent_id, kind, terminal, cost_usd, diff)
    except SandboxError as exc:
        await _record(governance, "executor.failed", agent_id, {"kind": kind, "reason": str(exc)})
        return _make_result(agent_id, f"[harness:{kind}] sandbox error: {exc}")


def _interaction_summary(event: ExecApprovalRequested | ExecInputRequested) -> str:
    if isinstance(event, ExecApprovalRequested):
        return f"approval:{event.capability}"
    return f"input:{event.question}"


async def _finish(
    governance: GovernanceProvider,
    agent_id: str,
    kind: str,
    terminal: ExecResult | None,
    cost_usd: float,
    diff: str,
) -> dict[str, Any]:
    if terminal is None:
        await _record(governance, "executor.result", agent_id, {"kind": kind, "status": "failure"})
        return _make_result(agent_id, f"[harness:{kind}] failure: no result event")

    await _record(
        governance,
        "executor.result",
        agent_id,
        {
            "kind": kind,
            "status": terminal.status,
            "cost_usd": cost_usd,
            "diff_bytes": len(diff),
            "exit_metadata": dict(terminal.exit_metadata),
        },
    )

    if terminal.status == "success":
        summary = terminal.output if isinstance(terminal.output, str) else "completed"
        note = f" (+{len(diff)} bytes diff)" if diff else ""
        return _make_result(agent_id, f"[harness:{kind}] {summary}{note}")

    reason = terminal.exit_metadata.get("reason") or terminal.exit_metadata.get("denied") or ""
    return _make_result(agent_id, f"[harness:{kind}] {terminal.status}: {reason}".rstrip(": "))
