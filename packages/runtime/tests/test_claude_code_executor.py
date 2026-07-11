"""claude-code adapter: stream-json → ExecEvent mapping + adapter behavior (executor P2, PR5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ClaudeCodeExecutor,
    ExecMessage,
    ExecResult,
    ExecStarted,
    ExecToolCall,
    ExecUsage,
    SandboxHandle,
    TaskSpec,
    default_executor_registry,
)
from swarmkit_runtime.executors._claude_code import _ClaudeCodeTranslator

FIXTURE = Path(__file__).parent / "fixtures" / "claude-code" / "stream-success.jsonl"


def _translate_fixture() -> tuple[_ClaudeCodeTranslator, list[object]]:
    tr = _ClaudeCodeTranslator()
    events: list[object] = []
    for line in FIXTURE.read_text().splitlines():
        events.extend(tr.feed(line))
    return tr, events


def test_translator_maps_stream_json_to_exec_events() -> None:
    tr, events = _translate_fixture()
    kinds = [type(e).__name__ for e in events]
    # system→(session captured, no event); two assistant turns (text+usage, tool+usage); result.
    assert kinds == [
        "ExecMessage",
        "ExecUsage",
        "ExecToolCall",
        "ExecUsage",
        "ExecUsage",
        "ExecResult",
    ]
    assert tr.session_id == "sess-abc123"


def test_translator_payloads() -> None:
    _tr, events = _translate_fixture()
    msg = next(e for e in events if isinstance(e, ExecMessage))
    assert msg.role == "assistant" and "add the flag" in msg.text

    tool = next(e for e in events if isinstance(e, ExecToolCall))
    assert tool.tool == "Edit" and tool.status == "called" and "config.py" in tool.input_summary

    first_usage = next(e for e in events if isinstance(e, ExecUsage))
    assert first_usage.input_tokens == 1200 and first_usage.cached_tokens == 800

    result = next(e for e in events if isinstance(e, ExecResult))
    assert result.status == "success"
    assert result.output == "Added the DEBUG flag."
    assert result.exit_metadata["session_id"] == "sess-abc123"
    assert result.exit_metadata["num_turns"] == 2
    # cost rides the terminal usage event.
    final_usage = [e for e in events if isinstance(e, ExecUsage)][-1]
    assert final_usage.cost_usd == pytest.approx(0.0123)


def test_translator_maps_max_turns_to_budget_exceeded() -> None:
    tr = _ClaudeCodeTranslator()
    events = tr.feed('{"type":"result","subtype":"error_max_turns","is_error":true,"num_turns":5}')
    result = next(e for e in events if isinstance(e, ExecResult))
    assert result.status == "budget_exceeded"


def test_build_command_maps_budget_and_model() -> None:
    ex = ClaudeCodeExecutor(model="claude-opus-4-8", extra_args=("--dangerously-skip-permissions",))
    cmd = ex._build_command(TaskSpec(statement="do the thing"), BudgetEnvelope(max_turns=7))
    assert cmd[0] == "claude"
    assert "-p" in cmd and "do the thing" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert "--dangerously-skip-permissions" in cmd


def test_preflight_fails_when_binary_missing() -> None:
    ex = ClaudeCodeExecutor(binary="claude-nonexistent-xyz")
    report = ex.preflight(TaskSpec(statement="x"), SandboxHandle(root=Path(".")))
    assert report.ok is False
    assert "not found" in (report.reason or "")


def test_from_config_and_registry_registration() -> None:
    ex = ClaudeCodeExecutor.from_config(
        {"binary": "/usr/bin/claude", "model": "m", "extra_args": []}
    )
    assert ex._binary == "/usr/bin/claude" and ex._model == "m"
    # registered in the default registry so `kind: claude-code` resolves + validates config.
    registry = default_executor_registry()
    assert "claude-code" in registry.kinds()
    resolved = registry.resolve(type("Blk", (), {"kind": "claude-code", "config": {"model": "m"}}))
    assert resolved.kind == "claude-code" and resolved.config == {"model": "m"}


class _ScriptedClaude(ClaudeCodeExecutor):
    """Adapter with a scripted line source — exercises run() without a real `claude` binary."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self._lines = lines

    async def _open_stream(self, cmd: list[str], cwd: Path, run_id: str) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_run_streams_started_then_translated_events_and_resume_token() -> None:
    lines = FIXTURE.read_text().splitlines()
    ex = _ScriptedClaude(lines)
    task = TaskSpec(statement="add a flag")
    sandbox = SandboxHandle(root=Path("."))

    events = [e async for e in ex.run(task, sandbox, BudgetEnvelope())]

    assert isinstance(events[0], ExecStarted)
    run_id = events[0].run_id
    assert isinstance(events[-1], ExecResult) and events[-1].status == "success"
    # session_id captured during the stream → resume token available for this run.
    token = ex.resume_token(run_id)
    assert token is not None and token.value == "sess-abc123"
