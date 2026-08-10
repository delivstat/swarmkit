"""`swarmkit run` records a job, so a CLI run appears in history.

There was exactly one writer of a job row in the codebase — serve's `JobService`, behind
`POST /run/{topology}`. `swarmkit run` built a runtime, executed the graph, printed and exited, so a
CLI run was invisible in the UI's jobs list and its history, even though it had produced a trace and
audit events. Anyone driving SwarmKit from the terminal had no record of what they had run.

The job id is the run's **thread id** on purpose. That is also the trace's `run_id`, so the row
points straight at `.swarmkit/traces/<id>.json` and at `/observability/runs/<id>/trace` — a link a
serve-started job cannot make, because it mints a separate id.

Recording is best-effort in one direction only: a store that will not open loses the RECORD of a
run, never the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from swarmkit_runtime._workspace_runtime import RunResult, UsageSummary
from swarmkit_runtime.cli import _cmd_run
from swarmkit_runtime.review._hitl import HITLDeferredError


class _Store:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str, str]] = []
        self.correlations: list[str | None] = []
        self.labels: list[dict[str, str]] = []
        self.updates: list[dict[str, Any]] = []

    def create_job(
        self,
        job_id: str,
        topology: str,
        user_input: str,
        correlation_id: str | None = None,
        source: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.created.append((job_id, topology, user_input, source or ""))
        self.correlations.append(correlation_id)
        self.labels.append(dict(labels or {}))

    def update_job(self, job_id: str, **fields: Any) -> None:
        self.updates.append({"job_id": job_id, **fields})


class _Runtime:
    """A runtime whose run() does whatever the test needs."""

    def __init__(self, result: Any = None, raises: BaseException | None = None) -> None:
        self._result = result
        self._raises = raises
        self.thread_ids: list[str] = []

    async def run(self, _topology: str, _input: str, *, thread_id: str, **_kw: Any) -> Any:
        self.thread_ids.append(thread_id)
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    s = _Store()
    monkeypatch.setattr(_cmd_run, "_job_store", lambda _ws: s)
    return s


def _run(runtime: Any, tmp_path: Path, **kw: Any) -> Any:
    return _cmd_run._execute_run(runtime, "hello", "say hi", tmp_path, **kw)


def test_the_correlation_id_and_labels_reach_the_row(store: _Store, tmp_path: Path) -> None:
    """`jobs.correlation_id` was NULL for every CLI run, breaking the trace chain at its first
    link. The store double is duck-typed, so this also guards the signature it has to match."""
    _run(
        _Runtime(result=RunResult(output="x")),
        tmp_path,
        correlation_id="WMS-30",
        labels={"map": "WMS-30"},
    )

    assert store.correlations == ["WMS-30"]
    assert store.labels == [{"map": "WMS-30"}]


# ---- the record exists -----------------------------------------------------------------------


def test_a_successful_run_is_recorded(store: _Store, tmp_path: Path) -> None:
    """The bug: nothing wrote a row, so the run never appeared anywhere."""
    result = RunResult(
        output="hi there", usage=UsageSummary(input_tokens=12, output_tokens=3, cost_usd=0.004)
    )

    _run(_Runtime(result=result), tmp_path)

    assert len(store.created) == 1, "the row must exist"
    assert store.created[0][1] == "hello"
    done = store.updates[-1]
    assert done["status"] == "completed"
    assert done["output"] == "hi there"


def test_the_row_is_created_before_the_run(store: _Store, tmp_path: Path) -> None:
    """A run that never returns must still be visible as started — otherwise a long or hung run is
    indistinguishable from one that was never launched."""
    runtime = _Runtime(raises=RuntimeError("boom"))
    with pytest.raises(typer.Exit):
        _run(runtime, tmp_path)

    assert store.created, "created before the failure, not after the success"


def test_usage_is_recorded(store: _Store, tmp_path: Path) -> None:
    """Tokens and cost are what the history table shows; without them a CLI row is a blank line."""
    result = RunResult(
        output="x", usage=UsageSummary(input_tokens=1200, output_tokens=340, cost_usd=0.42)
    )

    _run(_Runtime(result=result), tmp_path)

    done = store.updates[-1]
    assert done["usage_input_tokens"] == 1200
    assert done["usage_output_tokens"] == 340
    assert done["usage_cost_usd"] == 0.42


def test_the_job_id_is_the_thread_id(store: _Store, tmp_path: Path) -> None:
    """Which is also the trace's run_id — so the row links to its trace, and `--resume` and the
    history row name the same thing."""
    runtime = _Runtime(result=RunResult(output="x"))

    _run(runtime, tmp_path)

    assert store.created[0][0] == runtime.thread_ids[0]
    assert store.updates[-1]["job_id"] == runtime.thread_ids[0]


# ---- every exit path closes the row ----------------------------------------------------------


def test_a_failed_run_is_recorded_as_failed(store: _Store, tmp_path: Path) -> None:
    with pytest.raises(typer.Exit):
        _run(_Runtime(raises=RuntimeError("boom")), tmp_path)

    done = store.updates[-1]
    assert done["status"] == "failed"
    assert "boom" in done["error"]


def test_an_interrupted_run_is_not_called_failed(store: _Store, tmp_path: Path) -> None:
    """It is checkpointed and resumable. Calling that "failed" would misreport it, and StatusBadge
    renders an unknown status as a muted pill, so honesty costs nothing here."""
    with pytest.raises(typer.Exit):
        _run(_Runtime(raises=KeyboardInterrupt()), tmp_path)

    done = store.updates[-1]
    assert done["status"] == "interrupted"
    assert "resumable" in done["error"]


def test_a_deferred_review_is_recorded_as_deferred(store: _Store, tmp_path: Path) -> None:
    """A run waiting on a human is not a failure either."""
    with pytest.raises(typer.Exit):
        _run(
            _Runtime(raises=HITLDeferredError("designer", "spec-review", "needs sign-off")),
            tmp_path,
        )

    done = store.updates[-1]
    assert done["status"] == "deferred"
    assert "needs sign-off" in done["error"]


def test_an_unknown_topology_closes_the_row(store: _Store, tmp_path: Path) -> None:
    """Otherwise the row sits "running" forever — the stalled-saga shape."""
    with pytest.raises(typer.Exit):
        _run(_Runtime(raises=KeyError("no such topology 'nope'")), tmp_path)

    assert store.updates[-1]["status"] == "failed"


@pytest.mark.parametrize(
    "exc", [RuntimeError("x"), KeyboardInterrupt(), KeyError("k")], ids=lambda e: type(e).__name__
)
def test_every_exit_path_closes_the_row(exc: BaseException, store: _Store, tmp_path: Path) -> None:
    """Stated as one property: whatever happens, the row does not stay open — a row stuck at
    `running` is the stalled-saga shape, indistinguishable from work still in flight."""
    with pytest.raises(typer.Exit):
        _run(_Runtime(raises=exc), tmp_path)

    assert store.updates, f"{type(exc).__name__} wrote no closing update"
    assert store.updates[-1].get("completed_at"), f"{type(exc).__name__} left the row open"


# ---- recording never costs the run -----------------------------------------------------------


def test_a_store_that_will_not_open_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one-directional rule: losing the record is acceptable, losing the work is not."""
    monkeypatch.setattr(_cmd_run, "_job_store", lambda _ws: None)

    result = _run(_Runtime(result=RunResult(output="still ran")), tmp_path)

    assert result.output == "still ran"


def test_a_store_that_fails_mid_write_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Broken(_Store):
        def update_job(self, job_id: str, **fields: Any) -> None:
            raise OSError("disk went away")

    monkeypatch.setattr(_cmd_run, "_job_store", lambda _ws: _Broken())

    result = _run(_Runtime(result=RunResult(output="still ran")), tmp_path)

    assert result.output == "still ran"
