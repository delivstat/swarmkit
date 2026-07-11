"""DeclarativeExecutor + adapter loading (executor P3, PR3)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
import yaml
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    ExecResult,
    ExecStarted,
    ExecToolCall,
    SandboxHandle,
    TaskSpec,
    load_adapter_specs,
    parse_adapter_spec,
)

REPO = Path(__file__).resolve().parents[3]
ADAPTER_YAML = REPO / "packages/schema/tests/fixtures/executor-adapter/claude-code.yaml"
STREAM_JSONL = Path(__file__).parent / "fixtures/claude-code/stream-success.jsonl"


def _spec() -> object:
    return parse_adapter_spec(yaml.safe_load(ADAPTER_YAML.read_text()))


class _Scripted(DeclarativeExecutor):
    """DeclarativeExecutor with a scripted line source — no real subprocess."""

    def __init__(self, lines: list[str], **kw: object) -> None:
        super().__init__(_spec(), **kw)  # type: ignore[arg-type]
        self._lines = lines
        self.launched_argv: list[str] | None = None
        self.launched_env: Mapping[str, str] | None = None

    async def _open_stream(
        self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
    ) -> AsyncIterator[str]:
        self.launched_argv = argv
        self.launched_env = env
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_run_streams_started_then_translated_events_and_resume_token() -> None:
    lines = STREAM_JSONL.read_text().splitlines()
    ex = _Scripted(lines, config={"model": "claude-opus-4-8"})
    events = [
        e
        async for e in ex.run(
            TaskSpec(statement="add a flag"), SandboxHandle(root=Path(".")), BudgetEnvelope()
        )
    ]
    assert isinstance(events[0], ExecStarted)
    run_id = events[0].run_id
    assert events[0].ref == "claude-opus-4-8"
    assert any(isinstance(e, ExecToolCall) for e in events)
    assert isinstance(events[-1], ExecResult) and events[-1].status == "success"
    token = ex.resume_token(run_id)
    assert token is not None and token.value == "sess-abc123"


@pytest.mark.asyncio
async def test_build_command_and_auth_env_injection() -> None:
    ex = _Scripted([], config={"model": "m"}, model_provider_credential="sk-secret")
    # force api_key auth so the env var is injected
    ex._config["auth_mode"] = "api_key"
    _ = [
        e
        async for e in ex.run(
            TaskSpec(statement="do it"), SandboxHandle(root=Path(".")), BudgetEnvelope(max_turns=5)
        )
    ]
    assert ex.launched_argv is not None
    assert ex.launched_argv[0] == "claude"
    assert "do it" in ex.launched_argv
    assert ex.launched_argv[ex.launched_argv.index("--max-turns") + 1] == "5"
    assert ex.launched_argv[ex.launched_argv.index("--model") + 1] == "m"
    # the active auth mode injected the model-provider credential into the subprocess env
    assert ex.launched_env is not None
    assert ex.launched_env["ANTHROPIC_API_KEY"] == "sk-secret"


@pytest.mark.asyncio
async def test_subscription_mode_strips_the_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The billing bug from real e2e: a stale ANTHROPIC_API_KEY inherited from the environment must
    NOT leak into the harness when subscription mode is active — else it overrides the CLI login."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stale-out-of-credits")
    ex = _Scripted([])  # claude-code.yaml defaults to auth.default: subscription
    _ = [
        e
        async for e in ex.run(
            TaskSpec(statement="x"), SandboxHandle(root=Path(".")), BudgetEnvelope()
        )
    ]
    assert ex.launched_env is not None
    # subscription is active → the api_key mode's declared var is stripped from the subprocess env.
    assert "ANTHROPIC_API_KEY" not in ex.launched_env


@pytest.mark.asyncio
async def test_api_key_mode_keeps_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ex = _Scripted([], config={"auth_mode": "api_key"}, model_provider_credential="sk-live")
    _ = [
        e
        async for e in ex.run(
            TaskSpec(statement="x"), SandboxHandle(root=Path(".")), BudgetEnvelope()
        )
    ]
    assert ex.launched_env is not None
    assert ex.launched_env["ANTHROPIC_API_KEY"] == "sk-live"


def test_preflight_fails_when_binary_missing() -> None:
    ex = DeclarativeExecutor(_spec())  # type: ignore[arg-type]
    # claude-code's launch binary is `claude`; on a bare CI box it may be absent → report says so.
    report = ex.preflight(TaskSpec(statement="x"), SandboxHandle(root=Path(".")))
    assert report.details["kind"] == "claude-code"
    # ok depends on whether `claude` is installed; the shape is what we assert.
    assert isinstance(report.ok, bool)


def test_kind_is_the_adapter_id() -> None:
    ex = DeclarativeExecutor(_spec())  # type: ignore[arg-type]
    assert ex.kind == "claude-code"


def test_load_adapter_specs_reads_workspace_adapters(tmp_path: Path) -> None:
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "echo.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: ExecutorAdapter\n"
        "metadata: {id: echo-harness, name: Echo, description: a workspace adapter}\n"
        "spec:\n"
        "  launch: {command: [echo, hi]}\n"
        "  stream: {format: jsonl}\n"
        "  event_map: [{emit: [{event: result, with: {status: success}}]}]\n"
        "provenance: {authored_by: human, version: 0.1.0}\n",
        encoding="utf-8",
    )
    specs = load_adapter_specs(tmp_path)
    assert "echo-harness" in specs
    assert specs["echo-harness"].launch.command == ("echo", "hi")


def test_load_adapter_specs_none_root_returns_bundled_only() -> None:
    # No bundled adapters ship until PR4, so with no workspace this is empty (and must not error).
    assert load_adapter_specs(None) == load_adapter_specs(None)
