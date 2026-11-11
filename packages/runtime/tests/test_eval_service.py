"""EvalService — one durable, recoverable eval run (design/details/eval-on-serve.md §2, §4).

A scripted fake runtime and a real SQLite store: what is asserted is the row the service leaves
behind, since that is what every front door reads.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from swarmkit_runtime.eval import (
    EvalRunConflictError,
    EvalRunNotFoundError,
    EvalService,
    EvalSetNotFoundError,
    EvalTargetNotFoundError,
)
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.persistence import SqliteStore

pytestmark = pytest.mark.asyncio

_SET = """\
apiVersion: swarmkit/v1
kind: EvalSet
metadata: {id: greet, description: Greets people.}
target: hello
cases:
  - {id: one, input: "say one", expect: {contains: [one]}}
  - {id: two, input: "say two", expect: {contains: [two]}}
  - {id: three, input: "say three", expect: {contains: [three]}}
"""


class _Runtime:
    """Echoes the input; records every input it ran; optionally fails, raises, or blocks."""

    def __init__(self, *, cost: float = 0.0, fail: set[str] | None = None) -> None:
        self.ran: list[str] = []
        self.cost = cost
        self.fail = fail or set()
        self.block_on: str | None = None
        self.blocked = asyncio.Event()
        self.workspace = SimpleNamespace(topologies={"hello": object(), "hello@1.0.0": object()})

    async def run(self, topology_name: str, user_input: str) -> object:
        self.ran.append(user_input)
        if self.block_on and self.block_on in user_input:
            self.blocked.set()
            await asyncio.sleep(3600)
        output = "nothing" if any(f in user_input for f in self.fail) else user_input
        return SimpleNamespace(output=output, events=[], usage=SimpleNamespace(cost_usd=self.cost))

    async def judge(
        self, *, skill_id: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult:
        return DecisionSkillResult(skill_id=skill_id, verdict="pass", confidence=1.0, reasoning="")

    async def judge_rubric(
        self, *, rubric: str, content: str, trigger: str = "eval"
    ) -> DecisionSkillResult:
        return DecisionSkillResult(skill_id="r", verdict="pass", confidence=1.0, reasoning="")

    async def judge_reference(
        self, *, reference: str, content: str, case_input: str = "", trigger: str = "eval"
    ) -> DecisionSkillResult:
        return DecisionSkillResult(skill_id="r", verdict="pass", confidence=1.0, reasoning="")


def _ws(tmp_path: Path, body: str = _SET) -> Path:
    (tmp_path / "evals").mkdir(exist_ok=True)
    (tmp_path / "evals" / "greet.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def _service(tmp_path: Path, runtime: _Runtime | None = None) -> tuple[EvalService, _Runtime]:
    rt = runtime or _Runtime()
    return EvalService(rt, SqliteStore(tmp_path), _ws(tmp_path)), rt


async def test_a_run_records_every_case_and_finishes_with_its_report(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, _Runtime(cost=0.01))
    seen: list[str] = []

    row = await service.run("greet", source="cli", on_case=lambda c: seen.append(c.case_id))

    assert row.status == "succeeded"
    assert [c["case_id"] for c in row.cases] == ["one", "two", "three"] == seen
    assert (row.passed, row.total) == (3, 3)
    assert row.cost_usd == pytest.approx(0.03)
    assert row.report["pass_rate"] == 1.0 and row.report["finished_at"]
    assert row.source == "cli" and row.finished_at is not None
    assert row.diff is None, "no prior run — nothing to diff against"


async def test_a_failing_case_makes_the_run_failed(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, _Runtime(fail={"two"}))
    row = await service.run("greet", source="cli")
    assert row.status == "failed" and row.passed == 2


async def test_the_diff_is_against_the_previous_finished_run(tmp_path: Path) -> None:
    rt = _Runtime()
    service, _ = _service(tmp_path, rt)
    first = await service.run("greet", source="cli")
    rt.fail = {"two"}
    second = await service.run("greet", source="serve")

    assert second.diff is not None
    assert second.diff["prior_eval_run_id"] == first.id
    assert second.diff["regressed"] == ["two"]
    assert second.diff["prev_pass_rate"] == 1.0


async def test_the_diff_falls_back_to_a_legacy_report_file(tmp_path: Path) -> None:
    """Reports written by `swarmkit eval` before 1.267.0 still count as history."""
    service, _ = _service(tmp_path, _Runtime(fail={"one"}))
    legacy = tmp_path / ".swarmkit" / "eval-results"
    legacy.mkdir(parents=True)
    (legacy / "greet-20260101T000000Z.json").write_text(
        json.dumps({"pass_rate": 1.0, "cases": [{"case_id": "one", "passed": True}]})
    )
    row = await service.run("greet", source="cli")
    assert row.diff is not None
    assert row.diff["prior_eval_run_id"] is None
    assert row.diff["regressed"] == ["one"]
    assert row.diff["new"] == ["three", "two"]


async def test_an_unknown_set_or_target_raises_before_any_row(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(EvalSetNotFoundError):
        await service.run("nope", source="cli")
    _ws(tmp_path, _SET.replace("target: hello", "target: missing"))
    with pytest.raises(EvalTargetNotFoundError, match="missing"):
        await service.run("greet", source="cli")
    assert service.list_runs() == []


async def test_a_pinned_target_version_is_accepted(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _ws(tmp_path, _SET.replace("target: hello", "target: hello@1.0.0"))
    assert (await service.run("greet", source="cli")).status == "succeeded"


async def test_an_eval_that_breaks_ends_in_error_not_running(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    def _boom(_case: object) -> None:
        raise RuntimeError("the reporter broke")

    row = await service.run("greet", source="cli", on_case=_boom)
    assert row.status == "error"
    assert "the reporter broke" in (row.error or "")


async def test_a_case_that_times_out_fails_and_the_eval_carries_on(tmp_path: Path) -> None:
    rt = _Runtime()
    rt.block_on = "two"
    service, _ = _service(tmp_path, rt)
    row = await service.run("greet", source="cli", case_timeout_s=0.2)
    assert row.status == "failed"
    two = next(c for c in row.cases if c["case_id"] == "two")
    assert "timeout" in two["error"]
    assert [c["case_id"] for c in row.cases] == ["one", "two", "three"]


async def test_a_stop_takes_effect_between_cases(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    run_id = "stop-me"

    def _stop_after_first(c: object) -> None:
        service.request_stop(run_id)

    row = await service.run("greet", source="cli", eval_run_id=run_id, on_case=_stop_after_first)
    assert row.status == "stopped"
    assert [c["case_id"] for c in row.cases] == ["one"]
    assert row.diff is None, "a partial report is not diffed"


async def test_resume_runs_only_the_cases_without_results(tmp_path: Path) -> None:
    rt = _Runtime(cost=0.5)
    service, _ = _service(tmp_path, rt)
    stopped = await service.run("greet", source="cli", should_stop=lambda: len(rt.ran) >= 1)
    assert stopped.status == "stopped" and rt.ran == ["say one"]

    resumed = await service.resume(stopped.id)

    assert resumed.id == stopped.id
    assert rt.ran == ["say one", "say two", "say three"], "no case ran twice"
    assert resumed.status == "succeeded" and (resumed.passed, resumed.total) == (3, 3)
    assert resumed.cost_usd == pytest.approx(1.5), "each case is charged once"
    assert [c["case_id"] for c in resumed.cases] == ["one", "two", "three"]


async def test_resume_runs_the_set_as_it_was_not_as_edited(tmp_path: Path) -> None:
    rt = _Runtime()
    service, _ = _service(tmp_path, rt)
    stopped = await service.run("greet", source="cli", should_stop=lambda: len(rt.ran) >= 1)
    _ws(tmp_path, _SET.replace('"say two"', '"edited two"').replace("target: hello", "target: x"))

    resumed = await service.resume(stopped.id)

    assert "say two" in rt.ran and "edited two" not in rt.ran
    assert resumed.target == "hello"


async def test_resume_is_refused_for_a_finished_or_unknown_run(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    done = await service.run("greet", source="cli")
    with pytest.raises(EvalRunConflictError, match="succeeded"):
        await service.resume(done.id)
    with pytest.raises(EvalRunNotFoundError):
        await service.resume("no-such-run")


async def test_resume_is_refused_when_the_target_is_gone(tmp_path: Path) -> None:
    rt = _Runtime()
    service, _ = _service(tmp_path, rt)
    stopped = await service.run("greet", source="cli", should_stop=lambda: len(rt.ran) >= 1)
    rt.workspace.topologies = {}
    with pytest.raises(EvalTargetNotFoundError):
        await service.resume(stopped.id)
    assert service.get_run(stopped.id).status == "stopped"  # type: ignore[union-attr]


async def test_a_cancelled_eval_is_marked_interrupted_and_resumable(tmp_path: Path) -> None:
    """A process shutting down mid-case (serve stopping, Ctrl-C) says so on the row."""
    rt = _Runtime()
    rt.block_on = "two"
    service, _ = _service(tmp_path, rt)
    task = asyncio.create_task(service.run("greet", source="serve", eval_run_id="cancel-me"))
    await asyncio.wait_for(rt.blocked.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = service.get_run("cancel-me")
    assert row is not None and row.status == "interrupted"
    assert [c["case_id"] for c in row.cases] == ["one"]

    rt.block_on = None
    resumed = await service.resume("cancel-me")
    assert resumed.status == "succeeded"
    assert rt.ran == ["say one", "say two", "say two", "say three"], (
        "only the case in flight when it was cancelled runs again"
    )


async def test_the_heartbeat_moves_while_a_case_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import swarmkit_runtime.eval._service as svc  # noqa: PLC0415

    monkeypatch.setattr(svc, "HEARTBEAT_INTERVAL_S", 0.05)
    rt = _Runtime()
    rt.block_on = "two"
    service, _ = _service(tmp_path, rt)
    task = asyncio.create_task(service.run("greet", source="cli", eval_run_id="beat"))
    await asyncio.wait_for(rt.blocked.wait(), 5)
    first = service.get_run("beat").heartbeat_at  # type: ignore[union-attr]
    await asyncio.sleep(0.3)
    later = service.get_run("beat").heartbeat_at  # type: ignore[union-attr]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert later > first, "a long case must not look like a dead process"


async def test_list_sets_carries_the_last_run(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    [before] = service.list_sets()
    assert before.id == "greet" and before.case_count == 3 and before.last_run is None
    row = await service.run("greet", source="cli")
    [after] = service.list_sets()
    assert after.last_run is not None and after.last_run.id == row.id
    assert after.name == "greet", "name defaults to the id"
