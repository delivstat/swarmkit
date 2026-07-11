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
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime.executors._adapter_spec import AdapterSpec, parse_adapter_spec
from swarmkit_runtime.executors._event_map import AdapterInterpreter, build_command
from swarmkit_runtime.executors._events import ExecEvent, ExecRaw, ExecStarted
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


def _ctx(
    task: TaskSpec, sandbox: SandboxHandle, budget: BudgetEnvelope, config: Mapping[str, Any]
) -> dict[str, str]:
    """Build the closed substitution context from the run inputs + adapter config. Absent values
    are simply omitted (the template collapses them to empty)."""
    ctx: dict[str, str] = {"task.statement": task.statement, "sandbox.root": str(sandbox.root)}
    if task.base_ref:
        ctx["task.base_ref"] = task.base_ref
    if budget.max_turns is not None:
        ctx["budget.max_turns"] = str(budget.max_turns)
    if budget.max_cost_usd is not None:
        ctx["budget.max_cost_usd"] = str(budget.max_cost_usd)
    if budget.max_wall_clock_minutes is not None:
        ctx["budget.max_wall_clock_minutes"] = str(budget.max_wall_clock_minutes)
    for key, value in config.items():
        if isinstance(value, (str, int, float, bool)):
            ctx[f"config.{key}"] = str(value)
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
        Overridable seam — tests substitute a scripted line source without a real binary."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active[run_id] = proc
        try:
            stdout = proc.stdout
            assert stdout is not None
            async for line in stdout:
                yield line.decode(errors="replace")
            await proc.wait()
        finally:
            self._active.pop(run_id, None)

    async def run(
        self, task: TaskSpec, sandbox: SandboxHandle, budget: BudgetEnvelope
    ) -> AsyncIterator[ExecEvent]:
        import json  # noqa: PLC0415

        run_id = uuid.uuid4().hex
        interp = AdapterInterpreter(self._spec)
        ctx = _ctx(task, sandbox, budget, self._config)
        argv = build_command(self._spec, ctx) + self._auth_args(ctx)
        env = self._launch_env(ctx)

        yield ExecStarted(run_id=run_id, kind=self._spec.kind, ref=self._config.get("model"))
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
                yield event
            if interp.session_id is not None:
                self._sessions[run_id] = interp.session_id

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
