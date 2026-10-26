"""A harness's exit report — usage written to a file, not to stdout (#1090).

GitHub Copilot CLI puts no token counts on its JSONL stream; they exist only in the file its
``--usage-output-file`` writes when the process exits. Without ``spec.report`` such a run is
recorded as 0 tokens and $0.00. These tests pin the seam: the path is substituted, the file is read
after the stream ends, its events arrive BEFORE the terminal result (consumers stop at the first
result), and a missing or broken report degrades to "no usage" rather than a failed run.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import ValidationError
from swarmkit_runtime.executors import (
    REPORT_FILE_NAME,
    AdapterInterpreter,
    BudgetEnvelope,
    DeclarativeExecutor,
    ExecMessage,
    ExecResult,
    ExecUsage,
    SandboxHandle,
    TaskSpec,
    enforce_budget,
    parse_adapter_spec,
)
from swarmkit_schema import validate

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "packages/schema/tests/fixtures/executor-adapter/usage-report.yaml"

_STREAM = [
    json.dumps({"type": "msg", "text": "working"}),
    json.dumps({"type": "done", "text": "fixed it"}),
]
_REPORT = {
    "models": {
        "vendor/model-a.5": {"usage": {"in": 1200, "out": 80}},
        "vendor/model-b": {"usage": {"in": 300, "out": 20}},
    }
}


def _raw() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(FIXTURE.read_text())
    # a message rule, so a turn is visible before the result
    raw["spec"]["event_map"].insert(
        0,
        {
            "when": {"type": "msg"},
            "emit": [{"event": "message", "with": {"role": "assistant", "text": "$.text"}}],
        },
    )
    return raw


class _Scripted(DeclarativeExecutor):
    """Streams scripted lines and, like the real harness, writes the report as it exits."""

    def __init__(self, raw: Mapping[str, Any], report: str | None) -> None:
        super().__init__(parse_adapter_spec(raw))
        self._report = report
        self.argv: list[str] = []

    async def _open_stream(
        self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
    ) -> AsyncIterator[str]:
        self.argv = argv
        for line in _STREAM:
            yield line
        if self._report is not None:
            (cwd / argv[argv.index("--usage-file") + 1]).write_text(self._report)


async def _run(ex: DeclarativeExecutor, root: Path) -> list[object]:
    stream = ex.run(TaskSpec(statement="fix it"), SandboxHandle(root=root), BudgetEnvelope())
    return [e async for e in enforce_budget(stream, BudgetEnvelope())]


def test_the_report_fixture_validates() -> None:
    validate("executor-adapter", yaml.safe_load(FIXTURE.read_text()))
    spec = parse_adapter_spec(yaml.safe_load(FIXTURE.read_text()))
    assert spec.report is not None and len(spec.report.event_map) == 1


def test_a_report_needs_an_event_map() -> None:
    bad = REPO / "packages/schema/tests/fixtures/executor-adapter-invalid"
    with pytest.raises(ValidationError):
        validate(
            "executor-adapter", yaml.safe_load((bad / "report-without-event-map.yaml").read_text())
        )


def test_for_each_over_an_object_iterates_its_values() -> None:
    """A report keyed by model id: the id contains the path separator, so only values work."""
    spec = parse_adapter_spec(yaml.safe_load(FIXTURE.read_text()))
    assert spec.report is not None
    events = AdapterInterpreter(spec, rules=spec.report.event_map).feed(_REPORT)
    usage = [e for e in events if isinstance(e, ExecUsage)]
    assert [(u.input_tokens, u.output_tokens) for u in usage] == [(1200, 80), (300, 20)]


@pytest.mark.asyncio
async def test_report_usage_arrives_before_the_terminal_result(tmp_path: Path) -> None:
    ex = _Scripted(_raw(), json.dumps(_REPORT))
    events = await _run(ex, tmp_path)

    assert ex.argv[ex.argv.index("--usage-file") + 1] == REPORT_FILE_NAME
    usage = [e for e in events if isinstance(e, ExecUsage)]
    assert sum(u.input_tokens for u in usage) == 1500
    assert sum(u.output_tokens for u in usage) == 100
    # enforce_budget stops at the first result — usage after it would have been dropped
    assert isinstance(events[-1], ExecResult) and events[-1].status == "success"
    assert any(isinstance(e, ExecMessage) for e in events)
    # read once, then removed — it must not be diffed as the agent's work or read by the next run
    assert not (tmp_path / REPORT_FILE_NAME).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [None, "{not json", "[]"])
async def test_a_missing_or_broken_report_is_no_usage_not_a_failure(
    tmp_path: Path, report: str | None
) -> None:
    events = await _run(_Scripted(_raw(), report), tmp_path)
    assert not any(isinstance(e, ExecUsage) for e in events)
    assert isinstance(events[-1], ExecResult) and events[-1].status == "success"


@pytest.mark.asyncio
async def test_a_stale_report_from_an_earlier_run_is_not_read(tmp_path: Path) -> None:
    """A persistent working dir outlives the run: last run's numbers must not become this run's."""
    (tmp_path / REPORT_FILE_NAME).write_text(json.dumps(_REPORT))
    events = await _run(_Scripted(_raw(), None), tmp_path)
    assert not any(isinstance(e, ExecUsage) for e in events)


@pytest.mark.asyncio
async def test_without_a_report_block_nothing_changes(tmp_path: Path) -> None:
    raw = _raw()
    del raw["spec"]["report"]
    raw["spec"]["launch"]["command"] = ["report-harness", "{task.statement}"]
    (tmp_path / REPORT_FILE_NAME).write_text(json.dumps(_REPORT))

    class _Plain(_Scripted):
        async def _open_stream(
            self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
        ) -> AsyncIterator[str]:
            self.argv = argv
            for line in _STREAM:
                yield line

    ex = _Plain(raw, None)
    events = await _run(ex, tmp_path)
    assert not any(isinstance(e, ExecUsage) for e in events)
    assert isinstance(events[-1], ExecResult)
    # a file the runtime did not ask for is the harness's (or the repo's) — left alone
    assert (tmp_path / REPORT_FILE_NAME).exists()


@pytest.mark.asyncio
async def test_a_result_rule_in_the_report_cannot_override_the_stream(tmp_path: Path) -> None:
    raw = _raw()
    raw["spec"]["report"]["event_map"].append(
        {"emit": [{"event": "result", "with": {"status": "failure"}}]}
    )
    events = await _run(_Scripted(raw, json.dumps(_REPORT)), tmp_path)
    results = [e for e in events if isinstance(e, ExecResult)]
    assert [r.status for r in results] == ["success"]
