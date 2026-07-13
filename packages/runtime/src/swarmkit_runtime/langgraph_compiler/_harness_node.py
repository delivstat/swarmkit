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

import logging
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
    ExecMessage,
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
    SandboxSpec,
    TaskSpec,
    collect_diff,
    container_sandbox,
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
from swarmkit_runtime.trust import TrustStore

from ._helpers import _make_result
from ._input_classifier import classify_input_request, should_classify
from ._relay import resolve_input, resolve_relay
from ._run_context import current_parent_agent

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.langgraph_compiler._state import SwarmState
    from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
    from swarmkit_runtime.resolver import ResolvedAgent
    from swarmkit_runtime.review import ReviewQueue

# Default idle timeout (seconds): the never-hang backstop when a harness config sets none.
_DEFAULT_IDLE_SECONDS = 120.0
# Park-resume: cap the run → resolve → relaunch loop so a harness that keeps requesting new
# capabilities can't spin forever (each round is a real relaunch + spend).
_MAX_RELAY_ROUNDS = 5

_logger = logging.getLogger(__name__)


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
    driver_mode: str | None = None  # the adapter's interaction.driver (e.g. "park-resume")
    driver: InteractionDriver = field(default_factory=NoInteractionDriver)
    review_queue: ReviewQueue | None = None
    max_wait_seconds: float | None = None
    topology_id: str = ""
    # §6.3 input escalation: a model provider + classifier model enable question detection. Absent ⇒
    # input requests are not detected (the harness node stays permission-relay + abort).
    model_provider: ModelProviderProtocol | None = None
    classifier_model: str | None = None
    # §6.2.3 trust accrual: repeated operator approvals of an (archetype, capability) accrue toward
    # an allowlist-changeset proposal. Absent (no root / no archetype) ⇒ no accrual.
    trust: TrustStore | None = None
    archetype: str | None = None


def _relay_ctx(
    agent: ResolvedAgent,
    root: Path | None,
    driver: InteractionDriver | None,
    review_queue: ReviewQueue | None,
    model_provider: ModelProviderProtocol | None = None,
) -> _RelayCtx:
    """Assemble the relay context from the adapter spec + injected (test) overrides."""
    on_unanswerable = "abort"
    driver_mode: str | None = None
    max_wait: float | None = None
    if root is not None:
        spec = load_adapter_specs(root).get(agent.executor.kind)
        if spec is not None:
            on_unanswerable = spec.on_unanswerable
            driver_mode = spec.interaction_driver
            max_wait = spec.max_approval_wait_seconds
    return _RelayCtx(
        on_unanswerable=on_unanswerable,
        driver_mode=driver_mode,
        driver=driver if driver is not None else NoInteractionDriver(),
        model_provider=model_provider,
        classifier_model=agent.executor.config.get("classifier_model"),
        review_queue=review_queue
        if review_queue is not None
        else (FileReviewQueue(root) if root else None),
        max_wait_seconds=max_wait,
        trust=TrustStore(root, threshold=_trust_threshold(agent)) if root else None,
        archetype=agent.source_archetype,
    )


def _trust_threshold(agent: ResolvedAgent) -> int:
    """Operator-tunable N (approvals before an allowlist proposal) — from the archetype's
    ``executor.config.trust_threshold``; defaults to the store's built-in (5)."""
    raw = agent.executor.config.get("trust_threshold")
    try:
        return int(raw) if raw is not None else 5
    except (TypeError, ValueError):
        return 5


def _container_disabled() -> bool:
    """The global kill-switch: ``SWARMKIT_DISABLE_CONTAINER_SANDBOX`` forces the native worktree for
    every archetype regardless of adapter config (executor-container-sandbox.md). Always wins, so an
    environment with no container runtime is never trapped by an archetype that insists on one."""
    return os.environ.get("SWARMKIT_DISABLE_CONTAINER_SANDBOX", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _sandbox_for(
    agent: ResolvedAgent,
    root: Path,
    base_ref: str,
    sandbox_spec: SandboxSpec | None = None,
    env_keys: tuple[str, ...] = (),
) -> tuple[Any, bool]:
    """Choose the harness's execution sandbox. Precedence (most-specific first, but *disable always
    wins*):

    1. ``SWARMKIT_DISABLE_CONTAINER_SANDBOX`` set → native worktree, whatever the adapter says.
    2. adapter/config ``sandbox.kind == container`` → the container tier (opt-in).
    3. ``executor.config.working_dir`` set → a persistent directory (session-scoped harness).
    4. else → an isolated, ephemeral git worktree (the default; today's behaviour).

    Returns ``(context_manager, persistent)``."""
    spec = _effective_sandbox(agent, sandbox_spec)
    if spec is not None and spec.is_container and not _container_disabled():
        return container_sandbox(root, base_ref, spec, env_keys=env_keys), False
    if spec is not None and spec.is_container and _container_disabled():
        _logger.info("container sandbox disabled by env; using native worktree for %s", agent.id)

    working_dir = agent.executor.config.get("working_dir")
    if working_dir:
        wd = Path(working_dir)
        resolved = wd if wd.is_absolute() else (root / wd)
        return _persistent_dir(resolved.resolve()), True
    return worktree_sandbox(root, base_ref), False


def _effective_sandbox(
    agent: ResolvedAgent, from_adapter: SandboxSpec | None
) -> SandboxSpec | None:
    """Merge the adapter's ``sandbox`` block with a per-archetype ``executor.config.sandbox``
    override. The override wins (same pattern as ``working_dir`` / ``allowed_tools``); absent both
    ⇒ ``None`` (worktree)."""
    override = agent.executor.config.get("sandbox")
    if override:
        return SandboxSpec.from_raw(dict(override))
    return from_adapter


async def run_harness_node(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
    *,
    workspace_root: Path | Any = None,
    executor: Executor | None = None,
    driver: InteractionDriver | None = None,
    review_queue: ReviewQueue | None = None,
    model_provider: ModelProviderProtocol | None = None,
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
    relay = _relay_ctx(agent, root, driver, review_queue, model_provider)
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

    adapter_spec = load_adapter_specs(root).get(agent.executor.kind)
    sandbox_spec = adapter_spec.sandbox if adapter_spec is not None else None
    env_keys = adapter_spec.env_keys() if adapter_spec is not None else ()
    sandbox_cm, persistent = _sandbox_for(
        agent, root, task.base_ref or "HEAD", sandbox_spec, env_keys
    )
    try:
        async with sandbox_cm as sandbox:
            report = runner.preflight(task, sandbox)
            if not report.ok:
                await _record(
                    governance, "executor.failed", agent_id, {"kind": kind, "reason": report.reason}
                )
                return _make_result(agent_id, f"[harness:{kind}] preflight failed: {report.reason}")

            await _record(governance, "executor.started", agent_id, {"kind": kind})

            park_resume = (
                relay.on_unanswerable == "relay"
                and relay.driver_mode == "park-resume"
                and relay.review_queue is not None
            )
            terminal: ExecResult | None = None
            resume_token: str | None = None
            pending_answer: str | None = None
            granted: list[str] = []
            answered: dict[str, str] = {}  # §6.3 session memo: never ask the same question twice
            rounds = 0
            while True:
                round_denials: list[ExecApprovalRequested] = []
                round_messages: list[str] = []
                round_native_input: ExecInputRequested | None = None
                round_did_work = False
                aborted = False
                stream = runner.run(
                    task,
                    sandbox,
                    budget,
                    resume_token=resume_token,
                    granted=tuple(granted),
                    answer=pending_answer,
                )
                async for event in enforce_budget(stream, budget, cancel=_cancel):
                    if isinstance(event, ExecStarted):
                        holder["run_id"] = event.run_id
                    elif isinstance(event, ExecUsage):
                        meter.add_usage(event)
                    elif isinstance(event, ExecMessage):
                        round_messages.append(event.text)
                    elif isinstance(event, ExecToolCall):
                        round_did_work = True
                        meter.tool_calls.append(
                            ToolCall(tool_name=event.tool, result_length=len(event.input_summary))
                        )
                    elif isinstance(event, ExecApprovalRequested):
                        if park_resume:
                            round_denials.append(event)  # resolve after the stream, then relaunch
                            continue
                        await _cancel()  # no relay driver: deny / abort — never hang.
                        terminal = ExecResult(
                            status="needs_approval",
                            exit_metadata={"denied": _interaction_summary(event)},
                        )
                        aborted = True
                        break
                    elif isinstance(event, ExecInputRequested):
                        if park_resume:
                            round_native_input = event  # a native question event; resolve below
                            continue
                        await _cancel()
                        terminal = ExecResult(
                            status="needs_approval",
                            exit_metadata={"denied": _interaction_summary(event)},
                        )
                        aborted = True
                        break
                    elif isinstance(event, ExecResult):
                        terminal = event

                if aborted or not park_resume or rounds >= _MAX_RELAY_ROUNDS:
                    break

                # (a) permission denials → relaunch with the expanded grant (§6.2)
                if round_denials:
                    newly = await _resolve_denials(
                        round_denials, agent_id, governance, relay, set(granted)
                    )
                    token = runner.resume_token(holder["run_id"] or "")
                    if not newly or token is None:
                        break  # nothing new approved / can't resume — denied terminal stands
                    granted.extend(newly)
                    resume_token, pending_answer = token.value, None
                    rounds += 1
                    continue

                # (b) input request → relaunch with the resolved answer (§6.3)
                request = round_native_input or await _detect_input(
                    round_messages, did_work=round_did_work, relay=relay
                )
                if request is None or request.question in answered:
                    break  # no question (or already answered this session) — done
                ans = await resolve_input(
                    request,
                    agent_id=agent_id,
                    topology_id=relay.topology_id,
                    governance=governance,
                    review_queue=relay.review_queue,  # type: ignore[arg-type]
                    max_wait_seconds=relay.max_wait_seconds,
                )
                token = runner.resume_token(holder["run_id"] or "")
                if not ans or token is None:
                    break  # no answer / can't resume — never hangs
                answered[request.question] = ans
                resume_token, pending_answer = token.value, ans
                rounds += 1

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


async def _resolve_denials(
    denials: list[ExecApprovalRequested],
    agent_id: str,
    governance: GovernanceProvider,
    relay: _RelayCtx,
    already_granted: set[str],
) -> list[str]:
    """Resolve each denied capability via the relay orchestrator (policy → inbox → wait). Returns
    the newly-approved capabilities to expand the grant on the next relaunch."""
    assert relay.review_queue is not None  # park_resume guarantees it
    newly: list[str] = []
    for req in denials:
        if not req.capability or req.capability in already_granted or req.capability in newly:
            continue
        decision = await resolve_relay(
            req,
            agent_id=agent_id,
            topology_id=relay.topology_id,
            governance=governance,
            review_queue=relay.review_queue,
            max_wait_seconds=relay.max_wait_seconds,
            trust=relay.trust,
            archetype=relay.archetype,
        )
        if decision.granted:
            newly.append(req.capability)
    return newly


async def _detect_input(
    messages: list[str], *, did_work: bool, relay: _RelayCtx
) -> ExecInputRequested | None:
    """Detect a §6.3 input request from the harness's final message via the shared classifier —
    only when a model provider + classifier model are configured, the run took no action
    (structural pre-filter), and there is a final message. No model ⇒ no detection (opt-in)."""
    if relay.model_provider is None or not relay.classifier_model or not messages:
        return None
    if not should_classify(artifact_present=did_work):
        return None
    return await classify_input_request(
        messages[-1], model_provider=relay.model_provider, model=relay.classifier_model
    )


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
