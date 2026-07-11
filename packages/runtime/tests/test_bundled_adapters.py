"""The bundled reference adapter library — codex / opencode / gemini-cli (executor P3, PR5).

Each harness is added with ZERO core code: an ``adapter.yaml`` in the bundled library + a captured
stream fixture. These tests prove (a) every bundled adapter validates against the canonical schema,
(b) it loads as a runnable ``DeclarativeExecutor``, and (c) its declared event map normalizes its
harness's stream into ExecEvents — the proof of the plugin contract (RFC acceptance #3).
"""

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
    load_adapter_specs,
    parse_adapter_spec,
)
from swarmkit_schema import validate

BUNDLED_DIR = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/executors/adapters"
STREAMS = Path(__file__).parent / "fixtures/harness-streams"

# harness id -> (expected result status, a substring expected somewhere in messages/result output).
# opencode is verified against the real binary (fixture captured from opencode 1.17.x); codex and
# gemini-cli are experimental (representative fixtures, not yet run against real binaries).
CASES = {
    "codex": ("success", "DEBUG=1"),
    "opencode": ("success", "created the file"),
    "gemini-cli": ("success", "Refactored"),
}


def _bundled_ids() -> list[str]:
    return sorted(p.stem for p in BUNDLED_DIR.glob("*.yaml"))


def test_the_library_ships_the_expected_harnesses() -> None:
    ids = _bundled_ids()
    for expected in ("claude-code", "codex", "opencode", "gemini-cli"):
        assert expected in ids


@pytest.mark.parametrize("adapter_id", _bundled_ids())
def test_every_bundled_adapter_validates_against_the_schema(adapter_id: str) -> None:
    raw = yaml.safe_load((BUNDLED_DIR / f"{adapter_id}.yaml").read_text())
    validate("executor-adapter", raw)  # canonical JSON-Schema validation
    spec = parse_adapter_spec(raw)
    assert spec.kind == adapter_id


def test_load_adapter_specs_bundled_only_returns_the_library() -> None:
    specs = load_adapter_specs(None)  # no workspace → bundled only
    for expected in ("claude-code", "codex", "opencode", "gemini-cli"):
        assert expected in specs


@pytest.mark.parametrize("adapter_id", sorted(CASES))
def test_bundled_adapter_normalizes_its_harness_stream(adapter_id: str) -> None:
    expected_status, expected_output_substr = CASES[adapter_id]
    spec = parse_adapter_spec(yaml.safe_load((BUNDLED_DIR / f"{adapter_id}.yaml").read_text()))
    interp = AdapterInterpreter(spec)

    events: list[object] = []
    for raw in (STREAMS / f"{adapter_id}.jsonl").read_text().splitlines():
        line = raw.strip()
        if line:
            events.extend(interp.feed(json.loads(line)))

    # Every harness normalizes into the SAME vocabulary — a message, a tool call, usage, a result.
    assert any(isinstance(e, ExecMessage) for e in events)
    assert any(isinstance(e, ExecToolCall) for e in events)
    assert any(isinstance(e, ExecUsage) for e in events)
    results = [e for e in events if isinstance(e, ExecResult)]
    assert len(results) == 1  # collapsed to a single terminal result
    assert results[0].status == expected_status
    # the answer text may ride the terminal result's output OR the assistant messages
    haystack = str(results[0].output) + " ".join(
        e.text for e in events if isinstance(e, ExecMessage)
    )
    assert expected_output_substr in haystack
    # session captured for resume where the harness provides one
    assert interp.session_id is not None
