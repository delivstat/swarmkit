"""A failed stage stops the pipeline; its error is never the next stage's prompt.

Reported against 1.133.0. When a stage's agent failed, the failure message became that stage's
**output** artifact and was then handed to the next stage as its **input**. On WMS-1:

| stage  | artifact | bytes | content                                         |
|--------|----------|-------|-------------------------------------------------|
| triage | input    | 7,803 | the ticket — correct                            |
| triage | output   |    46 | `[harness:claude-code] failure: no result event` |
| design | input    |    46 | *the same 46 bytes*                             |
| design | output   |    91 | `I'm ready to help — what would you like to work on?` |

The design agent behaved correctly: asked for a task, it asked what the task was. The gate then
parked on that reply and asked a human to approve work that was never attempted, while the saga
reported `parked` throughout.

Three failures compound: the pipeline advances **past** a failure; the original error is destroyed
when the downstream output replaces it; and an agent is billed for a run that cannot succeed.

Why a string check was not the fix. The report suggested reusing `_is_error_passthrough`, but that
predicate matches `Error:` / `Tool error:` / `ToolError:` and does **not** match
`[harness:claude-code] failure: no result event` — it would have let this exact bug straight
through. A harness failure is not an exception either (a dead run is a normal terminal event), so
there was nothing structural to check. Hence `node_errors`: the node says it failed, in a field,
and no caller has to read prose to find out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._workspace_runtime import RunResult
from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.langgraph_compiler._drift import _is_error_passthrough
from swarmkit_runtime.langgraph_compiler._helpers import _make_failure, _make_result
from swarmkit_runtime.orchestration._saga import SagaState
from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage

HARNESS_FAILURE = "[harness:claude-code] failure: no result event"


class _SagaStore:
    def __init__(self) -> None:
        self.saga = SagaState(correlation_id="WMS-1", graph_id="g", input="the ticket")

    def get(self, _cid: str) -> SagaState:
        return self.saga

    def save(self, _saga: SagaState) -> None:
        pass


class _Runtime:
    """A runtime whose stage fails the way a harness fails: no exception, an error as output."""

    def __init__(self, root: Path, result: RunResult) -> None:
        self.workspace_root = root
        self._result = result
        self.inputs: list[str] = []

    async def run(self, _topology: str, stage_input: str, **_kw: Any) -> RunResult:
        self.inputs.append(stage_input)
        return self._result


def _run_stage(tmp_path: Path, result: RunResult) -> tuple[Any, _Runtime, Any]:
    ws = tmp_path / "ws"
    (ws / ".swarmkit").mkdir(parents=True, exist_ok=True)
    store = build_artifact_store(
        None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 'a.sqlite'}"
    )
    runtime = _Runtime(ws, result)
    return build_pipeline_run_stage(runtime, store, _SagaStore()), runtime, store  # type: ignore[arg-type]


# ---- the failure is reported as a failure ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_stage_reports_failed_not_completed(tmp_path: Path) -> None:
    """The bug at its root: the stage looked successful to everything downstream."""
    failed = RunResult(output=HARNESS_FAILURE, node_errors={"designer": HARNESS_FAILURE})
    run_stage, _rt, _store = _run_stage(tmp_path, failed)

    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t"})
    assert outcome.status == "failed", "a stage whose agent failed must not report success"


@pytest.mark.asyncio
async def test_the_error_is_the_failure_reason_not_an_artifact_to_approve(tmp_path: Path) -> None:
    """The error belongs in `detail`, where a human reads "triage failed" — not rendered as a
    document for review."""
    failed = RunResult(output=HARNESS_FAILURE, node_errors={"designer": HARNESS_FAILURE})
    run_stage, _rt, _store = _run_stage(tmp_path, failed)

    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t"})
    assert HARNESS_FAILURE in outcome.detail


@pytest.mark.asyncio
async def test_a_failed_gated_stage_does_not_park_for_a_human(tmp_path: Path) -> None:
    """A reviewer was asked to approve or reject work that was never attempted. A failed stage must
    fail, not park — even when the stage declares a gate."""
    failed = RunResult(output=HARNESS_FAILURE, node_errors={"designer": HARNESS_FAILURE})
    run_stage, _rt, _store = _run_stage(tmp_path, failed)

    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t", "gate": "review"})
    assert outcome.status == "failed", "parking on a failure asks a human to review nothing"


@pytest.mark.asyncio
async def test_a_successful_stage_is_unaffected(tmp_path: Path) -> None:
    """The guard: ordinary stages must behave exactly as before."""
    ok = RunResult(output="a real triage document")
    run_stage, _rt, _store = _run_stage(tmp_path, ok)

    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t"})
    assert outcome.status == "completed"
    assert not outcome.detail


@pytest.mark.asyncio
async def test_a_successful_gated_stage_still_parks(tmp_path: Path) -> None:
    ok = RunResult(output="a real triage document")
    run_stage, _rt, _store = _run_stage(tmp_path, ok)

    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t", "gate": "review"})
    assert outcome.status == "parked"


# ---- the error never becomes a prompt ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_error_is_never_handed_to_the_next_stage(tmp_path: Path) -> None:
    """End to end, as observed: triage fails, and design must not be prompted with its error."""
    failed = RunResult(output=HARNESS_FAILURE, node_errors={"designer": HARNESS_FAILURE})
    run_stage, runtime, _store = _run_stage(tmp_path, failed)

    await run_stage("WMS-1", {"id": "triage", "topology": "t"})
    # The controller stops on `failed`, but if anything did drive the next stage, it must not be
    # fed the error. Assert on the seam rather than trusting the sequencer above it.
    await run_stage("WMS-1", {"id": "design", "topology": "t"})

    assert HARNESS_FAILURE not in runtime.inputs[-1], (
        "the next stage was prompted with the previous stage's error"
    )


@pytest.mark.asyncio
async def test_a_result_object_without_the_field_is_not_treated_as_failed(tmp_path: Path) -> None:
    """`runtime.run` is a seam: a caller may hand back any result-shaped object. One without
    `node_errors` has no failures to report — it must neither crash nor be read as a failure.
    (Sixteen existing tests use exactly such a stub, which is how this was caught.)"""

    class _Bare:
        output = "a real triage document"

    class _BareRuntime:
        workspace_root = tmp_path / "ws"

        async def run(self, *_a: Any, **_k: Any) -> Any:
            return _Bare()

    (tmp_path / "ws" / ".swarmkit").mkdir(parents=True, exist_ok=True)
    run_stage = build_pipeline_run_stage(
        _BareRuntime(),  # type: ignore[arg-type]
        build_artifact_store(
            None,
            workspace_root=tmp_path / "ws",
            database_url=f"sqlite:///{tmp_path / 'ws' / '.swarmkit' / 'b.sqlite'}",
        ),
        _SagaStore(),  # type: ignore[arg-type]
    )
    outcome = await run_stage("WMS-1", {"id": "triage", "topology": "t"})
    assert outcome.status == "completed"


# ---- why a string check was not enough ---------------------------------------------------------


def test_the_existing_string_predicate_would_not_have_caught_this() -> None:
    """Justifies the structural marker over the suggested `_is_error_passthrough` reuse: the
    predicate does not match the failure string this bug is about."""
    assert not _is_error_passthrough(HARNESS_FAILURE)
    assert _is_error_passthrough("Error: boom"), "sanity: it does match what it was written for"


def test_a_failure_is_marked_in_the_state_not_just_the_text() -> None:
    """`_make_failure` carries the same text (a human still reads it) plus the marker a caller
    checks."""
    ok = _make_result("designer", "a real document")
    bad = _make_failure("designer", HARNESS_FAILURE)

    assert "node_errors" not in ok
    assert bad["node_errors"] == {"designer": HARNESS_FAILURE}
    assert bad["output"] == HARNESS_FAILURE, "the reason stays readable"


def test_run_result_exposes_the_failure() -> None:
    assert not RunResult(output="fine").failed
    failed = RunResult(output=HARNESS_FAILURE, node_errors={"designer": HARNESS_FAILURE})
    assert failed.failed
    assert "designer" in failed.failure_reason


def test_every_harness_failure_path_is_marked() -> None:
    """There are several ways a harness run dies — unavailable, no workspace root, empty input,
    preflight, sandbox error, no result event, non-success terminal. Every one must mark the state;
    one missed path is a silently-chained error again."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()
    # The single remaining plain result is the SUCCESS return in `_finish`.
    assert src.count("_make_result(") == 1, "a harness failure path is still reporting as success"
    assert src.count("_make_failure(") >= 6
