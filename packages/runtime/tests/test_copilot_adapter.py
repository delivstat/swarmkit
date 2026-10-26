"""The bundled GitHub Copilot CLI adapter (#1091), on a stream recorded from Copilot CLI 1.0.103.

Three things about Copilot shape this adapter and are pinned here:

- Its JSONL carries no token counts. They come from the ``--usage-output-file`` report, read at exit
  (``spec.report``, #1090) — so usage must arrive before the terminal result.
- A denied tool does not end the session; the model retries it every turn. The grant therefore goes
  through ``--available-tools`` (never offered) and ``max_turns`` is the backstop — one
  ``assistant.message`` per model turn is what the budget enforcer counts.
- Its ``result`` event carries no answer text; the harness node takes the last assistant message.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
import yaml
from swarmkit_runtime.executors import (
    REPORT_FILE_NAME,
    AdapterInterpreter,
    BudgetEnvelope,
    DeclarativeExecutor,
    ExecMessage,
    ExecResult,
    ExecToolCall,
    ExecUsage,
    SandboxHandle,
    TaskSpec,
    enforce_budget,
    load_adapter_specs,
    parse_adapter_spec,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._harness_node import _finish, _price_key
from swarmkit_runtime.mcp._gateway import GATEWAY_SERVER_NAME
from swarmkit_schema import validate

ADAPTER = (
    Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/executors/adapters/copilot.yaml"
)
STREAMS = Path(__file__).parent / "fixtures/harness-streams"
STREAM = (STREAMS / "copilot.jsonl").read_text().splitlines()
REPORT = (STREAMS / "copilot-usage.json").read_text()


def _raw() -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load(ADAPTER.read_text())
    return raw


class _Copilot(DeclarativeExecutor):
    """The real adapter; only the subprocess is scripted. Like Copilot, it writes the usage report
    at exit to wherever ``--usage-output-file`` pointed."""

    def __init__(self, lines: list[str], report: str | None = REPORT, **kw: object) -> None:
        super().__init__(parse_adapter_spec(_raw()), **kw)  # type: ignore[arg-type]
        self._lines = lines
        self._report = report
        self.argv: list[str] = []
        self.env: Mapping[str, str] = {}

    async def _open_stream(
        self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
    ) -> AsyncIterator[str]:
        self.argv, self.env = argv, env
        for line in self._lines:
            yield line
        if self._report is not None:
            (cwd / argv[argv.index("--usage-output-file") + 1]).write_text(self._report)


async def _run(
    ex: DeclarativeExecutor,
    root: Path,
    task: TaskSpec | None = None,
    budget: BudgetEnvelope | None = None,
) -> list[object]:
    budget = budget or BudgetEnvelope()
    stream = ex.run(task or TaskSpec(statement="fix calc.py"), SandboxHandle(root=root), budget)
    return [e async for e in enforce_budget(stream, budget)]


def test_it_validates_and_ships_in_the_library() -> None:
    validate("executor-adapter", _raw())
    assert "copilot" in load_adapter_specs(None)


def test_the_stream_normalizes_turns_tools_outcomes_and_session() -> None:
    spec = parse_adapter_spec(_raw())
    interp = AdapterInterpreter(spec)
    events = [e for line in STREAM if line.strip() for e in interp.feed(json.loads(line))]

    # One message per model turn — four turns in the recording, three of them tool-only.
    messages = [e for e in events if isinstance(e, ExecMessage)]
    assert len(messages) == 4
    assert messages[-1].text == "DONE"
    tools = [e for e in events if isinstance(e, ExecToolCall)]
    assert [t.tool for t in tools if t.status != "error"] == ["view", "view", "edit"]
    # The denied `edit` (recorded from a run whose grant left it out) surfaces as a failure.
    denied = [t for t in tools if t.status == "error"]
    assert len(denied) == 1 and "Permission denied" in denied[0].input_summary
    results = [e for e in events if isinstance(e, ExecResult)]
    assert [r.status for r in results] == ["success"]
    assert interp.session_id == "91a78c5c-d8b8-4b2d-b0b9-4a756ab5862a"
    # Nothing on the stream is usage — that is the reason the report exists.
    assert not any(isinstance(e, ExecUsage) for e in events)


@pytest.mark.asyncio
async def test_usage_comes_from_the_exit_report_before_the_result(tmp_path: Path) -> None:
    ex = _Copilot(STREAM)
    events = await _run(ex, tmp_path)

    usage = [e for e in events if isinstance(e, ExecUsage)]
    assert [
        (u.input_tokens, u.output_tokens, u.cached_tokens, u.reasoning_tokens) for u in usage
    ] == [(46253, 317, 22912, 84)]
    assert isinstance(events[-1], ExecResult) and events[-1].status == "success"
    assert ex.argv[ex.argv.index("--usage-output-file") + 1] == REPORT_FILE_NAME


@pytest.mark.asyncio
async def test_launch_is_headless_and_closes_the_ungoverned_paths(tmp_path: Path) -> None:
    ex = _Copilot(STREAM, config={"model": "openai/gpt-4.1-mini", "reasoning_effort": "low"})
    await _run(ex, tmp_path)
    argv = ex.argv
    assert argv[:3] == ["copilot", "-p", "fix calc.py"]
    for flag in (
        "--allow-all-tools",
        "--no-ask-user",
        "--no-auto-update",
        "--disable-builtin-mcps",
    ):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "openai/gpt-4.1-mini"
    assert argv[argv.index("--reasoning-effort") + 1] == "low"
    secrets = argv[argv.index("--secret-env-vars") + 1].split(",")
    assert {"COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY"} <= set(secrets)
    # No grant set ⇒ no tool restriction, and no MCP without a gateway.
    assert "--available-tools" not in argv
    assert "--additional-mcp-config" not in argv


@pytest.mark.asyncio
async def test_a_grant_names_gateway_tools_the_way_copilot_does(tmp_path: Path) -> None:
    """Copilot exposes an MCP tool as `<server>-<tool>` — seen live as
    `swarmkit-conventions__get_convention`. A grant in any other spelling would hide it."""
    task = TaskSpec(
        statement="fix calc.py",
        mcp_tools=("conventions__get_convention",),
        mcp_config=str(tmp_path / ".swarmkit-mcp.json"),
    )
    ex = _Copilot(STREAM, config={"allowed_tools": "view,edit"})
    await _run(ex, tmp_path, task)
    argv = ex.argv
    granted = argv[argv.index("--available-tools") + 1].split(",")
    assert granted == ["view", "edit", f"{GATEWAY_SERVER_NAME}-conventions__get_convention"]
    assert argv[argv.index("--additional-mcp-config") + 1] == f"@{tmp_path / '.swarmkit-mcp.json'}"


@pytest.mark.asyncio
async def test_api_key_mode_is_byok_and_subscription_strips_an_inherited_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "https://inherited.example/v1")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "sk-provider")

    byok = _Copilot(
        STREAM,
        config={
            "auth_mode": "api_key",
            "provider_base_url": "https://openrouter.ai/api/v1",
            "model": "moonshotai/kimi-k2.5",
        },
        model_provider_credential="sk-anthropic-must-not-leak",
    )
    await _run(byok, tmp_path)
    assert byok.env["COPILOT_PROVIDER_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert byok.env["COPILOT_PROVIDER_MODEL_ID"] == "moonshotai/kimi-k2.5"
    # The key is Copilot's own variable, never the node's ANTHROPIC/CODEX credential (#1097).
    assert byok.env["COPILOT_PROVIDER_API_KEY"] == "sk-provider"

    sub = _Copilot(STREAM, config={"auth_mode": "subscription"})
    await _run(sub, tmp_path)
    # An endpoint inherited from the shell must not silently turn a plan session into BYOK.
    assert "COPILOT_PROVIDER_BASE_URL" not in sub.env


@pytest.mark.asyncio
async def test_max_turns_ends_a_denial_loop(tmp_path: Path) -> None:
    """Live, a denied `edit` was retried for 55 turns until an outside timeout killed it. Each
    retry is a model turn, so `max_turns` stops it."""
    turn = [line for line in STREAM if '"type":"assistant.message"' in line][1]
    events = await _run(
        _Copilot([turn] * 50, report=None), tmp_path, budget=BudgetEnvelope(max_turns=5)
    )
    assert isinstance(events[-1], ExecResult)
    assert events[-1].status == "budget_exceeded"
    assert sum(isinstance(e, ExecMessage) for e in events) == 5


def test_a_harness_run_is_priced_by_its_model_not_its_adapter() -> None:
    """#1096 — `copilot` is in no price table, so a run priced by it was always $0.00."""
    assert _price_key({"model": "openai/gpt-4.1-mini"}, "copilot") == "openai/gpt-4.1-mini"
    assert _price_key({}, "claude-code") == "claude-code"
    assert _price_key({"model": ""}, "copilot") == "copilot"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "messages", "expected"),
    [
        (None, ["", "Fixed add() to return a + b.", ""], "Fixed add() to return a + b."),
        (None, ["", ""], "completed"),
        ("the harness's own answer", ["a later message"], "the harness's own answer"),
    ],
)
async def test_a_result_without_text_takes_the_last_message(
    output: str | None, messages: list[str], expected: str
) -> None:
    """Copilot's `result` carries status and usage only. The answer is its last message; a run
    that returned "completed" had already recorded it and thrown it away."""
    result = await _finish(
        MockGovernanceProvider(),
        "fixer",
        "copilot",
        ExecResult(status="success", output=output),
        0.0,
        "",
        session_messages=messages,
    )
    assert result["output"] == expected
