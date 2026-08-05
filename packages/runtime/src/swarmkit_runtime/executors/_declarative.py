"""The declarative executor + adapter loading (executor P3, PR3).

`DeclarativeExecutor` is the ONE engine that runs any harness described by an ``adapter.yaml``: it
builds the launch argv from the spec + a substitution context, spawns the subprocess, and streams
its stdout through the :class:`AdapterInterpreter` into normalized :data:`ExecEvent`s. No harness is
special-cased — a new harness is data (its adapter), never code.

`load_adapter_specs` discovers adapters from two sources: the bundled reference library shipped with
the runtime (``executors/adapters/*.yaml``) and a workspace's own ``adapters/`` directory (which may
override a bundled kind). The harness node's ``_build_executor`` looks a kind up here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime.executors._adapter_spec import AdapterSpec, parse_adapter_spec
from swarmkit_runtime.executors._event_map import AdapterInterpreter, build_command
from swarmkit_runtime.executors._events import ExecEvent, ExecRaw, ExecResult, ExecStarted
from swarmkit_runtime.executors._protocol import Executor
from swarmkit_runtime.executors._run import (
    BudgetEnvelope,
    PreflightReport,
    ResumeToken,
    SandboxHandle,
    TaskSpec,
)

# The bundled reference-adapter library (populated from PR4: claude-code, codex, opencode, …).
_BUNDLED_ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"

logger = logging.getLogger("swarmkit.executors")


class HarnessLineTooLongError(RuntimeError):
    """The harness emitted a single stdout line past the reader's limit."""


# How much harness stderr to retain for a failure report. Bounded: a chatty harness must not be
# able to grow the process, and the tail is what diagnoses an early exit.
_STDERR_TAIL_LINES = 200
_STDERR_DRAIN_TIMEOUT = 2.0

#: Per-line ceiling for the harness's stdout. Large enough that a real transcript line never hits
#: it (asyncio's 64 KiB default is hit by a single sizeable tool result), small enough to stay a
#: backstop against an unbounded stream. Overflow is reported WITH the size — the original failure
#: named nothing, so nobody could tell which tool produced it.
_STDOUT_LINE_LIMIT = 16 * 1024 * 1024


def _merged_tool_grant(task: TaskSpec, spec: Any, config: Mapping[str, Any]) -> str:
    """The harness's ``allowed_tools`` value: the operator's grant, plus the agent's gateway tools
    translated into this harness's own naming.

    Only when the operator HAS set a grant. Unset means "all tools", and turning an MCP grant into
    an allowlist would silently drop the built-ins (Read, Write, Bash) the agent also needs — a
    restriction nobody asked for is not a fix for a restriction that was too tight.

    The bug this closes: a grant is naturally written in the names the topology uses — skill ids —
    while the gateway advertises `<server>__<tool>` and the harness mangles that again. So the
    grant matched nothing and every real tool fell outside it. Constraining the agent was what
    broke it.
    """
    configured = str(config.get("allowed_tools") or "").strip()
    if not configured or not task.mcp_tools:
        return configured
    template = getattr(getattr(spec, "launch", None), "mcp_tool_name", "{tool}") or "{tool}"
    from swarmkit_runtime.mcp._gateway import GATEWAY_SERVER_NAME  # noqa: PLC0415

    gateway = GATEWAY_SERVER_NAME
    names = [template.replace("{tool}", t).replace("{gateway}", gateway) for t in task.mcp_tools]
    # Deduped, order preserved: the operator's entries first, then anything they did not name.
    have = {part.strip() for part in configured.replace(",", " ").split() if part.strip()}
    return ",".join([configured, *[n for n in names if n not in have]])


def _ctx(
    task: TaskSpec,
    sandbox: SandboxHandle,
    budget: BudgetEnvelope,
    config: Mapping[str, Any],
    spec: Any = None,
) -> dict[str, str]:
    """Build the closed substitution context from the run inputs + adapter config. Absent values
    are simply omitted (the template collapses them to empty)."""
    ctx: dict[str, str] = {"task.statement": task.statement, "sandbox.root": str(sandbox.root)}
    if task.base_ref:
        ctx["task.base_ref"] = task.base_ref
    if task.mcp_config:
        ctx["task.mcp_config"] = task.mcp_config
    if budget.max_turns is not None:
        ctx["budget.max_turns"] = str(budget.max_turns)
    if budget.max_cost_usd is not None:
        ctx["budget.max_cost_usd"] = str(budget.max_cost_usd)
    if budget.max_wall_clock_minutes is not None:
        ctx["budget.max_wall_clock_minutes"] = str(budget.max_wall_clock_minutes)
    for key, value in config.items():
        if isinstance(value, (str, int, float, bool)):
            ctx[f"config.{key}"] = str(value)
    grant = _merged_tool_grant(task, spec, config)
    if grant:
        ctx["config.allowed_tools"] = grant
    return ctx


class DeclarativeExecutor(Executor):
    """Runs a harness from its declarative :class:`AdapterSpec` — the engine, not a per-harness code
    adapter."""

    def __init__(
        self,
        spec: AdapterSpec,
        *,
        config: Mapping[str, Any] | None = None,
        model_provider_credential: str | None = None,
    ) -> None:
        self._spec = spec
        self._config = dict(config or {})
        self._credential = model_provider_credential
        self._active: dict[str, Any] = {}
        # run_id -> {"returncode": int | None, "stderr_tail": str}, populated when the process ends.
        self._exits: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, str] = {}

    @property
    def kind(self) -> str:  # type: ignore[override]
        # Per-instance: the adapter's id (base declares `kind` ClassVar for code executors).
        return self._spec.kind

    def config_schema(self) -> dict[str, Any]:
        # Adapter config knobs are open (model, flags); the launch template names what it uses.
        return {"type": "object", "additionalProperties": True}

    # ---- auth (generic: the active mode contributes env/args/credential_paths) -----------------

    def _active_auth_mode(self) -> str | None:
        auth = self._spec.auth
        if not auth.modes:
            return None
        chosen = self._config.get("auth_mode") or auth.default
        if chosen and chosen in auth.modes:
            return str(chosen)
        # Deterministic default: api_key precedence, else any declared mode.
        if "api_key" in auth.modes:
            return "api_key"
        return next(iter(auth.modes))

    def _launch_env(self, ctx: Mapping[str, str]) -> dict[str, str]:
        """Inherit the process env (so saved-CLI / subscription creds flow through), then make
        **only the active auth mode's** credentials effective: strip every *other* mode's declared
        env vars, and layer the adapter's launch env + the active mode's env.

        Stripping is what makes ``subscription`` mode actually use the subscription — a stale
        ``ANTHROPIC_API_KEY`` inherited from the environment would otherwise take precedence over
        the CLI login and break the run. The model-provider credential is the one secret (§7)."""
        env = dict(os.environ)
        active = self._active_auth_mode()
        for name, mode_spec in self._spec.auth.modes.items():
            if name != active:
                for var in mode_spec.env:
                    env.pop(var, None)
        sub_ctx = dict(ctx)
        if self._credential is not None:
            sub_ctx["credential.model_provider"] = self._credential
        from swarmkit_runtime.executors._event_map import _sub  # noqa: PLC0415

        def add(source: Mapping[str, str]) -> None:
            for name, tmpl in source.items():
                value = _sub(tmpl, sub_ctx)
                if value:  # never clobber an inherited var with an empty substitution
                    env[name] = value

        add(self._spec.launch.env)
        if active is not None:
            add(self._spec.auth.modes[active].env)
        return env

    def _auth_args(self, ctx: Mapping[str, str]) -> list[str]:
        mode = self._active_auth_mode()
        if mode is None:
            return []
        from swarmkit_runtime.executors._event_map import _sub  # noqa: PLC0415

        sub_ctx = dict(ctx)
        if self._credential is not None:
            sub_ctx["credential.model_provider"] = self._credential
        return [_sub(a, sub_ctx) for a in self._spec.auth.modes[mode].args]

    # ---- execution ------------------------------------------------------------------------------

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        binary = self._spec.launch.command[0] if self._spec.launch.command else ""
        resolved = (
            binary if os.path.isabs(binary) and os.path.exists(binary) else shutil.which(binary)
        )
        if resolved is None:
            return PreflightReport(
                ok=False,
                reason=f"harness binary {binary!r} not found on PATH",
                details={"kind": self._spec.kind, "binary": binary},
            )
        return PreflightReport(ok=True, details={"kind": self._spec.kind, "binary": resolved})

    async def _open_stream(
        self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
    ) -> AsyncIterator[str]:
        """Launch the subprocess and yield raw stdout lines; register it for :meth:`cancel`.
        Overridable seam — tests substitute a scripted line source without a real binary.

        stderr is drained CONCURRENTLY into a bounded tail. Piping it and never reading it lost
        every harness diagnostic — a CLI that died before emitting its terminal ``result`` event
        was recorded as the bare string "no result event", with the actual reason sitting unread in
        the pipe. It also deadlocked: a harness writing past the ~64KB pipe buffer blocks on write
        forever, so ``proc.wait()`` never returns.

        The tail + exit code land in :attr:`_exits` for :meth:`run` to attach to the terminal event.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Without this asyncio uses _DEFAULT_LIMIT (64 KiB) for the pipe's StreamReader, and a
            # harness emitting line-delimited JSON dies on the first turn carrying a large tool
            # result: "Separator is found, but chunk is longer than limit". A transcript line has
            # no natural 64 KiB bound — an MCP tool returning a 500 KB document is ordinary, not
            # exotic — and the failure aborted the run after earlier tool calls had succeeded,
            # naming neither the tool nor the size.
            limit=_STDOUT_LINE_LIMIT,
        )
        self._active[run_id] = proc
        tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        async def drain() -> None:
            stderr = proc.stderr
            if stderr is None:
                return
            async for raw in stderr:
                line = raw.decode(errors="replace").rstrip("\n")
                if line:
                    tail.append(line)
                    # debug, not info: a harness can print credentials.
                    logger.debug("[harness:%s stderr] %s", self._spec.kind, line)

        drainer = asyncio.create_task(drain())
        try:
            stdout = proc.stdout
            assert stdout is not None
            try:
                async for line in stdout:
                    yield line.decode(errors="replace")
            except ValueError as exc:
                # asyncio raises a bare "Separator is found, but chunk is longer than limit" with
                # no size and no context. Say what actually happened, at the layer that knows.
                raise HarnessLineTooLongError(
                    f"[harness:{self._spec.kind}] emitted a stdout line exceeding "
                    f"{_STDOUT_LINE_LIMIT} bytes ({exc}). This is usually one very large tool "
                    "result; raise the limit or have the tool paginate."
                ) from exc
            await proc.wait()
        finally:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(drainer, timeout=_STDERR_DRAIN_TIMEOUT)
            drainer.cancel()
            self._exits[run_id] = {
                "returncode": proc.returncode,
                "stderr_tail": "\n".join(tail),
            }
            self._active.pop(run_id, None)

    async def run(
        self,
        task: TaskSpec,
        sandbox: SandboxHandle,
        budget: BudgetEnvelope,
        *,
        resume_token: str | None = None,
        granted: tuple[str, ...] = (),
        answer: str | None = None,
    ) -> AsyncIterator[ExecEvent]:
        import json  # noqa: PLC0415

        run_id = uuid.uuid4().hex
        interp = AdapterInterpreter(self._spec)
        resuming = resume_token is not None
        # On a park-resume relaunch (RFC §6.2/§6.3): the resumed session's message is the resolved
        # input answer if one was supplied, else the declared permission-nudge; plus the resume
        # token + joined granted capabilities in the substitution context (all declared).
        run_task = task
        if resuming and answer:
            run_task = replace(task, statement=answer)
        elif resuming and self._spec.resume_prompt:
            run_task = replace(task, statement=self._spec.resume_prompt)
        ctx = _ctx(run_task, sandbox, budget, self._config, self._spec)
        if resuming:
            ctx["resume.token"] = resume_token or ""
            if granted:
                ctx["grant.capabilities"] = self._spec.grant_separator.join(granted)
        argv = build_command(self._spec, ctx, resuming=resuming) + self._auth_args(ctx)
        # The container tier prepends its `docker run … <image>` wrapper so the same argv runs
        # inside the container; a worktree leaves it empty (spawn directly, as today).
        if sandbox.exec_prefix:
            argv = [*sandbox.exec_prefix, *argv]
        env = self._launch_env(ctx)

        yield ExecStarted(run_id=run_id, kind=self._spec.kind, ref=self._config.get("model"))
        saw_terminal = False
        async for raw in self._open_stream(argv, env, sandbox.root, run_id):
            line = raw.strip()
            if not line:
                continue
            if self._spec.retain_raw:
                yield ExecRaw(line=line)
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for event in interp.feed(obj):
                saw_terminal = saw_terminal or isinstance(event, ExecResult)
                yield event
            if interp.session_id is not None:
                self._sessions[run_id] = interp.session_id

        # The harness exited without its terminal `result` event. Emit one carrying the process exit
        # code + the retained stderr tail, so the failure is diagnosable instead of being recorded
        # as the bare string "no result event" with the reason discarded.
        exit_info = self._exits.pop(run_id, None)
        if not saw_terminal:
            yield ExecResult(status="failure", exit_metadata=dict(exit_info or {}))

    async def cancel(self, run_id: str) -> None:
        proc = self._active.get(run_id)
        if proc is not None:
            proc.terminate()

    def resume_token(self, run_id: str) -> ResumeToken | None:
        session_id = self._sessions.get(run_id)
        return ResumeToken(value=session_id) if session_id else None


# ---- adapter loading ---------------------------------------------------------------------------


def _load_dir(directory: Path) -> dict[str, AdapterSpec]:
    specs: dict[str, AdapterSpec] = {}
    if not directory.is_dir():
        return specs
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping) and raw.get("kind") == "ExecutorAdapter":
            spec = parse_adapter_spec(raw)
            specs[spec.kind] = spec
    return specs


def load_adapter_specs(workspace_root: Path | str | None = None) -> dict[str, AdapterSpec]:
    """Discover declarative adapters. Bundled reference adapters load first; a workspace's own
    ``adapters/`` directory loads second and may override a bundled kind."""
    specs = _load_dir(_BUNDLED_ADAPTERS_DIR)
    if workspace_root is not None:
        specs.update(_load_dir(Path(workspace_root) / "adapters"))
    return specs


def load_workspace_adapter_specs(workspace_root: Path | str) -> dict[str, AdapterSpec]:
    """Only the workspace's own ``adapters/`` (not the bundled library). These are the adapters
    subject to the launch-block review gate (bundled ones are pre-vetted)."""
    return _load_dir(Path(workspace_root) / "adapters")
