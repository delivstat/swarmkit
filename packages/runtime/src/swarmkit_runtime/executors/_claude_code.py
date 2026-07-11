"""The ``claude-code`` executor adapter — Tier 1 reference (executor-abstraction §5, P2 PR5).

Launches Claude Code headless (``claude -p --output-format stream-json --verbose``) inside the
provisioned sandbox and translates its native stream-json into the normalized :data:`ExecEvent`
vocabulary — so a harness node is observed identically to any other executor. This is the reference
adapter that *proves the contract*; the ``codex`` adapter and the declarative Tier-2 path are P3+.

Config (validated at resolution against :meth:`config_schema`) reaches an executing instance via
:meth:`from_config` — the node builds a configured adapter per archetype (P2 PR6). The registry
holds a default instance purely for config validation.

Budget mapping is a backstop only: ``--max-turns`` is passed to the CLI, and core's
:func:`~swarmkit_runtime.executors._budget.enforce_budget` enforces the full envelope over the event
stream regardless of what the CLI honors. ``session_id`` from the stream becomes the resume token.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Protocol

from swarmkit_runtime.executors._events import (
    ExecEvent,
    ExecMessage,
    ExecResult,
    ExecResultStatus,
    ExecStarted,
    ExecToolCall,
    ExecUsage,
)
from swarmkit_runtime.executors._protocol import Executor
from swarmkit_runtime.executors._run import (
    BudgetEnvelope,
    PreflightReport,
    ResumeToken,
    SandboxHandle,
    TaskSpec,
)


class _Terminable(Protocol):
    def terminate(self) -> None: ...


def _summarize(value: Any, *, limit: int = 120) -> str:
    """Compact a tool input into a short, one-line summary for the event stream."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _ClaudeCodeTranslator:
    """Stateful translator: Claude Code stream-json lines → :data:`ExecEvent`s.

    Kept pure (no IO) so the mapping is unit-tested against a fixture of real stream-json lines.
    Captures ``session_id`` for the resume token. ``ExecStarted`` is emitted by the adapter (with
    the core run id), not here.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None

    def feed(self, raw: str) -> list[ExecEvent]:
        line = raw.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return []
        kind = obj.get("type")
        if kind == "system":
            session_id = obj.get("session_id")
            if isinstance(session_id, str):
                self.session_id = session_id
            return []
        if kind == "assistant":
            return self._assistant(obj)
        if kind == "result":
            return self._result(obj)
        # user (tool-result) turns and anything unrecognized carry no distinct signal in P2.
        return []

    def _assistant(self, obj: Mapping[str, Any]) -> list[ExecEvent]:
        message = obj.get("message") or {}
        events: list[ExecEvent] = []
        for block in message.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                events.append(ExecMessage(role="assistant", text=block.get("text", "")))
            elif block_type == "tool_use":
                events.append(
                    ExecToolCall(
                        tool=block.get("name", ""),
                        input_summary=_summarize(block.get("input")),
                        status="called",
                    )
                )
        usage = message.get("usage")
        if isinstance(usage, Mapping):
            events.append(self._usage(usage, cost_usd=None))
        return events

    def _result(self, obj: Mapping[str, Any]) -> list[ExecEvent]:
        subtype = obj.get("subtype")
        is_error = bool(obj.get("is_error", False))
        status: ExecResultStatus
        if subtype == "error_max_turns":
            status = "budget_exceeded"
        elif is_error:
            status = "failure"
        else:
            status = "success"
        events: list[ExecEvent] = []
        usage = obj.get("usage")
        cost = obj.get("total_cost_usd")
        cost_usd = float(cost) if isinstance(cost, (int, float)) else None
        if isinstance(usage, Mapping):
            events.append(self._usage(usage, cost_usd=cost_usd))
        elif cost_usd is not None:
            events.append(ExecUsage(cost_usd=cost_usd))
        events.append(
            ExecResult(
                status=status,
                output=obj.get("result"),
                exit_metadata={
                    "subtype": subtype,
                    "num_turns": obj.get("num_turns"),
                    "session_id": self.session_id,
                },
            )
        )
        return events

    @staticmethod
    def _usage(usage: Mapping[str, Any], *, cost_usd: float | None) -> ExecUsage:
        return ExecUsage(
            unit="tokens",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cached_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cost_usd=cost_usd,
        )


class ClaudeCodeExecutor(Executor):
    """The ``claude-code`` harness adapter (Tier 1 reference)."""

    kind: ClassVar[str] = "claude-code"

    def __init__(
        self,
        *,
        binary: str = "claude",
        model: str | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._binary = binary
        self._model = model
        self._extra_args = tuple(extra_args)
        self._active: dict[str, _Terminable] = {}
        self._sessions: dict[str, str] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ClaudeCodeExecutor:
        """Build a configured adapter from a resolved ``executor.config`` block (P2 PR6 wiring)."""
        return cls(
            binary=str(config.get("binary", "claude")),
            model=config.get("model"),
            extra_args=tuple(config.get("extra_args") or ()),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "binary": {"type": "string"},
                "model": {"type": "string"},
                "extra_args": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        }

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        """Fail fast before any spend: the binary must be resolvable. Credential presence is
        reported but not required — Claude Code also authenticates via subscription login, not only
        ``ANTHROPIC_API_KEY``."""
        resolved = (
            self._binary
            if os.path.isabs(self._binary) and os.path.exists(self._binary)
            else shutil.which(self._binary)
        )
        if resolved is None:
            return PreflightReport(
                ok=False,
                reason=f"claude binary {self._binary!r} not found on PATH",
                details={"binary": self._binary},
            )
        return PreflightReport(
            ok=True,
            details={"binary": resolved, "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY"))},
        )

    def _build_command(self, task: TaskSpec, budget: BudgetEnvelope) -> list[str]:
        cmd = [self._binary, "-p", task.statement, "--output-format", "stream-json", "--verbose"]
        if self._model:
            cmd += ["--model", self._model]
        if budget.max_turns is not None:
            cmd += ["--max-turns", str(budget.max_turns)]
        cmd += list(self._extra_args)
        return cmd

    async def _open_stream(self, cmd: list[str], cwd: Path, run_id: str) -> AsyncIterator[str]:
        """Launch the subprocess and yield raw stdout lines; register it for :meth:`cancel`.

        Overridable seam — tests substitute a scripted line source without a real ``claude`` binary.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
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
        run_id = uuid.uuid4().hex
        translator = _ClaudeCodeTranslator()
        yield ExecStarted(run_id=run_id, kind=self.kind, ref=self._model)
        cmd = self._build_command(task, budget)
        async for raw in self._open_stream(cmd, sandbox.root, run_id):
            for event in translator.feed(raw):
                yield event
            if translator.session_id is not None:
                self._sessions[run_id] = translator.session_id

    async def cancel(self, run_id: str) -> None:
        proc = self._active.get(run_id)
        if proc is not None:
            proc.terminate()

    def resume_token(self, run_id: str) -> ResumeToken | None:
        session_id = self._sessions.get(run_id)
        return ResumeToken(value=session_id) if session_id else None
