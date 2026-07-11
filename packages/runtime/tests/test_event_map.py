"""The declarative event-map interpreter + launch substitutor (executor P3, PR2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from swarmkit_runtime.executors import (
    AdapterInterpreter,
    ExecMessage,
    ExecResult,
    ExecToolCall,
    ExecUsage,
    build_command,
    parse_adapter_spec,
)
from swarmkit_runtime.executors._event_map import _get, _matches, _sub

REPO = Path(__file__).resolve().parents[3]
# The claude-code adapter YAML (schema fixture) + the P2 stream-json fixture — the migration proof:
# the DATA adapter must yield the same ExecEvents the P2 Python adapter did from the same bytes.
ADAPTER_YAML = REPO / "packages/schema/tests/fixtures/executor-adapter/claude-code.yaml"
STREAM_JSONL = Path(__file__).parent / "fixtures/claude-code/stream-success.jsonl"


def _spec() -> object:
    return parse_adapter_spec(yaml.safe_load(ADAPTER_YAML.read_text()))


def _run_stream() -> tuple[AdapterInterpreter, list[object]]:
    interp = AdapterInterpreter(_spec())  # type: ignore[arg-type]
    events: list[object] = []
    for raw in STREAM_JSONL.read_text().splitlines():
        line = raw.strip()
        if line:
            events.extend(interp.feed(json.loads(line)))
    return interp, events


def test_claude_code_yaml_produces_same_events_as_the_python_adapter() -> None:
    interp, events = _run_stream()
    kinds = [type(e).__name__ for e in events]
    # system (session captured); assistant text+usage; assistant tool+usage; result usage+result.
    assert kinds == [
        "ExecMessage",
        "ExecUsage",
        "ExecToolCall",
        "ExecUsage",
        "ExecUsage",
        "ExecResult",
    ]
    assert interp.session_id == "sess-abc123"


def test_event_payloads() -> None:
    _interp, events = _run_stream()
    msg = next(e for e in events if isinstance(e, ExecMessage))
    assert msg.role == "assistant" and "add the flag" in msg.text

    tool = next(e for e in events if isinstance(e, ExecToolCall))
    assert tool.tool == "Edit"

    first_usage = next(e for e in events if isinstance(e, ExecUsage))
    assert first_usage.input_tokens == 1200 and first_usage.output_tokens == 40

    result = next(e for e in events if isinstance(e, ExecResult))
    assert result.status == "success"
    assert result.output == "Added the DEBUG flag."
    final_usage = [e for e in events if isinstance(e, ExecUsage)][-1]
    assert final_usage.cost_usd == pytest.approx(0.0123)


def test_status_map_default_and_translation() -> None:
    interp = AdapterInterpreter(_spec())  # type: ignore[arg-type]
    # error_max_turns -> budget_exceeded via status_map
    e1 = interp.feed({"type": "result", "subtype": "error_max_turns"})
    assert isinstance(e1[-1], ExecResult) and e1[-1].status == "budget_exceeded"
    # an unmapped subtype -> _default (failure)
    e2 = interp.feed({"type": "result", "subtype": "who_knows"})
    assert isinstance(e2[-1], ExecResult) and e2[-1].status == "failure"


def test_get_walks_dicts_and_list_indices() -> None:
    obj = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert _get(obj, "a.b.1.c") == 2
    assert _get(obj, "a.b.9.c") is None  # out of range
    assert _get(obj, "a.x.y") is None  # missing


def test_matches_is_literal_equality() -> None:
    assert _matches({"type": "system"}, {"type": "system", "extra": 1})
    assert not _matches({"type": "system"}, {"type": "assistant"})


def test_for_each_without_array_emits_nothing() -> None:
    interp = AdapterInterpreter(_spec())  # type: ignore[arg-type]
    # assistant with content missing → for_each finds no array → no message/tool events (usage still
    # comes from the separate rule, but content-derived events are empty).
    events = interp.feed({"type": "assistant", "message": {"content": None}})
    assert not any(isinstance(e, (ExecMessage, ExecToolCall)) for e in events)


def test_build_command_substitutes_and_gates_optional_args() -> None:
    spec = _spec()
    ctx = {"task.statement": "add a flag", "budget.max_turns": "7", "config.model": ""}
    argv = build_command(spec, ctx)  # type: ignore[arg-type]
    assert argv[0] == "claude"
    assert "add a flag" in argv  # {task.statement} substituted
    assert argv[argv.index("--max-turns") + 1] == "7"  # optional group included (var set)
    assert "--model" not in argv  # config.model empty → group dropped


def test_build_command_appends_resume_args_only_when_resuming() -> None:
    spec = _spec()
    ctx = {"task.statement": "x", "resume.token": "sess-abc123"}
    assert "--resume" not in build_command(spec, ctx)  # type: ignore[arg-type]
    resumed = build_command(spec, ctx, resuming=True)  # type: ignore[arg-type]
    assert resumed[resumed.index("--resume") + 1] == "sess-abc123"


def test_sub_collapses_unresolved_vars() -> None:
    assert _sub("{missing}", {}) == ""
    assert _sub("pre-{x}-post", {"x": "MID"}) == "pre-MID-post"
