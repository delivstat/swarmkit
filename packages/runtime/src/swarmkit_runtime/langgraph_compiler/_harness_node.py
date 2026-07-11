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

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ClaudeCodeExecutor,
    ExecApprovalRequested,
    ExecInputRequested,
    ExecResult,
    ExecStarted,
    ExecUsage,
    Executor,
    ExecutorError,
    ResolvedExecutor,
    SandboxError,
    TaskSpec,
    collect_diff,
    enforce_budget,
    worktree_sandbox,
)
from swarmkit_runtime.governance import AuditEvent

from ._helpers import _make_result

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.langgraph_compiler._state import SwarmState
    from swarmkit_runtime.resolver import ResolvedAgent

# Default idle timeout (seconds): the never-hang backstop when a harness config sets none.
_DEFAULT_IDLE_SECONDS = 120.0


def _build_executor(resolved: ResolvedExecutor) -> Executor:
    """Construct a runnable, per-archetype-configured adapter for a resolved executor block.

    Only ``claude-code`` is runnable in P2 (``model`` never reaches here — the compiler runs it via
    its own node). An unknown/not-yet-runnable kind raises :class:`ExecutorError`.
    """
    if resolved.kind == "claude-code":
        return ClaudeCodeExecutor.from_config(resolved.config)
    raise ExecutorError(
        f"no runnable adapter for executor kind {resolved.kind!r} "
        "(P2 ships the 'claude-code' harness adapter)"
    )


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


async def run_harness_node(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    *,
    workspace_root: Path | Any = None,
    executor: Executor | None = None,
) -> dict[str, Any]:
    """Execute an agent whose ``executor.kind`` is not ``model``.

    Returns the standard node result dict. Errors (preflight, sandbox, no result) become a failure
    result, never an exception — a harness node faces the same downstream gates as any other node,
    so it fails on an edge rather than crashing the graph. ``executor`` is injectable for testing;
    in production it is built from ``agent.executor``.
    """
    agent_id = agent.id
    kind = agent.executor.kind
    root = Path(workspace_root) if workspace_root is not None else None

    try:
        runner = executor if executor is not None else _build_executor(agent.executor)
    except ExecutorError as exc:
        await _record(governance, "executor.failed", agent_id, {"kind": kind, "reason": str(exc)})
        return _make_result(agent_id, f"[harness:{kind}] unavailable: {exc}")

    if root is None:
        reason = "no workspace root available to provision a harness sandbox"
        await _record(governance, "executor.failed", agent_id, {"kind": kind, "reason": reason})
        return _make_result(agent_id, f"[harness:{kind}] {reason}")

    budget = _budget_from_config(dict(agent.executor.config))
    task = _task_spec(agent, state, root)
    return await _execute(agent, state, governance, runner, task, budget, root, kind)


async def _execute(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    runner: Executor,
    task: TaskSpec,
    budget: BudgetEnvelope,
    root: Path,
    kind: str,
) -> dict[str, Any]:
    agent_id = agent.id
    holder: dict[str, str | None] = {"run_id": None}

    async def _cancel() -> None:
        run_id = holder["run_id"]
        if run_id is not None:
            await runner.cancel(run_id)

    try:
        async with worktree_sandbox(root, task.base_ref or "HEAD") as sandbox:
            report = runner.preflight(task, sandbox)
            if not report.ok:
                await _record(
                    governance, "executor.failed", agent_id, {"kind": kind, "reason": report.reason}
                )
                return _make_result(agent_id, f"[harness:{kind}] preflight failed: {report.reason}")

            await _record(governance, "executor.started", agent_id, {"kind": kind})

            terminal: ExecResult | None = None
            cost_usd = 0.0
            guarded = enforce_budget(runner.run(task, sandbox, budget), budget, cancel=_cancel)
            async for event in guarded:
                if isinstance(event, ExecStarted):
                    holder["run_id"] = event.run_id
                elif isinstance(event, ExecUsage) and event.cost_usd is not None:
                    cost_usd += event.cost_usd
                elif isinstance(event, (ExecApprovalRequested, ExecInputRequested)):
                    # deny / abort (§6.2): no relay, no hang — terminate needs_approval.
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
                diff = await collect_diff(sandbox)

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
