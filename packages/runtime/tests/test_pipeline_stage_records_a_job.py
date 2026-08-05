"""A pipeline stage records a job, linked to the pipeline run that asked for it.

`swarmkit run` started recording jobs in 1.150.0 and serve's `POST /run/{topology}` always did.
A pipeline stage — which is also a topology run, and usually the expensive kind — recorded nothing.
So a pipeline showed saga state in `/runs` and nothing at all in `/jobs`: the actual work, its
output, its token cost, were findable from neither view.

Two things make a stage's job findable:

* the row is keyed by the stage's **run id** (`<correlation>:<stage>`), which is also the LangGraph
  thread and the trace's `run_id` — so the row points at its own trace;
* it carries **`correlation_id`**, so one pipeline run's stages can be selected by column rather
  than by parsing ids apart.

Recording stays one-directional, as everywhere else: losing the record is acceptable, losing the
run is not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.orchestration import SagaState
from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage


class _Store:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str, str, str]] = []
        self.updates: list[dict[str, Any]] = []

    def create_job(
        self,
        job_id: str,
        topology: str,
        user_input: str,
        correlation_id: str | None = None,
        source: str | None = None,
    ) -> None:
        self.created.append((job_id, topology, user_input, correlation_id or "", source or ""))

    def update_job(self, job_id: str, **fields: Any) -> None:
        self.updates.append({"job_id": job_id, **fields})


class _Usage:
    input_tokens = 1200
    output_tokens = 340
    cost_usd = 0.42


class _Result:
    def __init__(
        self, output: str = "the draft", node_errors: dict[str, str] | None = None
    ) -> None:
        self.output = output
        self.node_errors = node_errors or {}
        self.usage = _Usage()


class _Runtime:
    workspace_root = None

    def __init__(self, result: Any = None, raises: BaseException | None = None) -> None:
        self._result = result if result is not None else _Result()
        self._raises = raises
        self.thread_ids: list[str] = []

    async def run(self, _topology: str, _input: str, *, thread_id: str, **_kw: Any) -> Any:
        self.thread_ids.append(thread_id)
        if self._raises is not None:
            raise self._raises
        return self._result


class _SagaStore:
    def __init__(self) -> None:
        self.saga = SagaState(correlation_id="WMS-5", graph_id="g", input="the ticket")

    def get(self, _cid: str) -> SagaState:
        return self.saga

    def save(self, _saga: SagaState) -> None:
        pass


async def _run(job_store: Any, runtime: Any, tmp_path: Path, cid: str = "WMS-5") -> Any:
    """Run one stage of pipeline `cid` with a real artifact store, as the sibling stage tests do —
    the point under test is the job row, so everything around it stays real."""
    ws = tmp_path / "ws"
    (ws / ".swarmkit").mkdir(parents=True, exist_ok=True)
    artifacts = build_artifact_store(
        None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 'a.sqlite'}"
    )
    runtime.workspace_root = ws
    stage = build_pipeline_run_stage(
        runtime,
        artifacts,
        _SagaStore(),  # type: ignore[arg-type]
        job_store=job_store,
    )
    return await stage(cid, {"id": "design", "topology": "design-swarm"})


# ---- the record exists and points at its run -------------------------------------------------


@pytest.mark.asyncio
async def test_a_stage_is_recorded_as_a_job(tmp_path: Path) -> None:
    """The bug: a pipeline's topology runs wrote no job row at all."""
    store = _Store()

    await _run(store, _Runtime(), tmp_path)

    assert len(store.created) == 1, "the stage must leave a row"
    assert store.created[0][1] == "design-swarm"


@pytest.mark.asyncio
async def test_the_job_is_linked_to_the_pipeline_run(tmp_path: Path) -> None:
    """The link is the point. Without it a stage's job is a loose row nobody can trace back to the
    run that caused it."""
    store = _Store()

    await _run(store, _Runtime(), tmp_path, cid="WMS-5")

    assert store.created[0][3] == "WMS-5"


@pytest.mark.asyncio
async def test_the_job_id_is_the_stage_run_id(tmp_path: Path) -> None:
    """Which is also the thread id and the trace's run_id — so the row resolves to
    `/observability/runs/<id>/trace` with no extra mapping."""
    store = _Store()
    runtime = _Runtime()

    await _run(store, runtime, tmp_path)

    assert store.created[0][0] == "WMS-5:design"
    assert runtime.thread_ids == ["WMS-5:design"]


@pytest.mark.asyncio
async def test_the_row_exists_before_the_run(tmp_path: Path) -> None:
    """A stage that hangs must still be visible as started; otherwise long work and work never
    launched look identical."""
    store = _Store()

    await _run(store, _Runtime(raises=RuntimeError("boom")), tmp_path)

    assert store.created, "created before the outcome, not after success"


# ---- every exit path closes the row ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_stage_records_its_output_and_usage(tmp_path: Path) -> None:
    store = _Store()

    await _run(store, _Runtime(_Result(output="the draft")), tmp_path)

    done = store.updates[-1]
    assert done["status"] == "completed"
    assert done["output"] == "the draft"
    assert done["usage_input_tokens"] == 1200
    assert done["usage_cost_usd"] == 0.42


@pytest.mark.asyncio
async def test_a_raising_stage_is_recorded_as_failed(tmp_path: Path) -> None:
    store = _Store()

    await _run(store, _Runtime(raises=RuntimeError("boom")), tmp_path)

    assert store.updates[-1]["status"] == "failed"
    assert "boom" in store.updates[-1]["error"]


@pytest.mark.asyncio
async def test_an_unknown_topology_closes_the_row(tmp_path: Path) -> None:
    store = _Store()

    await _run(store, _Runtime(raises=KeyError("nope")), tmp_path)

    assert store.updates[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_a_node_that_failed_without_raising_is_recorded_as_failed(tmp_path: Path) -> None:
    """The harness shape: a run that dies is a normal terminal event, not an exception. It reaches
    the end of the stage with a result object, so it must not be recorded as completed."""
    store = _Store()

    await _run(store, _Runtime(_Result(node_errors={"designer": "no result event"})), tmp_path)

    assert store.updates[-1]["status"] == "failed"
    assert "no result event" in store.updates[-1]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime",
    [
        _Runtime(_Result()),
        _Runtime(raises=RuntimeError("x")),
        _Runtime(raises=KeyError("k")),
        _Runtime(_Result(node_errors={"n": "died"})),
    ],
    ids=["completed", "raised", "unknown-topology", "node-error"],
)
async def test_no_exit_path_leaves_the_row_open(runtime: Any, tmp_path: Path) -> None:
    """One property: a row stuck at `running` is the stalled-saga shape — indistinguishable from
    work still in flight."""
    store = _Store()

    await _run(store, runtime, tmp_path)

    assert store.updates, "nothing closed the row"
    assert store.updates[-1].get("completed_at")


# ---- recording never costs the stage ---------------------------------------------------------


@pytest.mark.asyncio
async def test_no_store_does_not_stop_the_stage(tmp_path: Path) -> None:
    outcome = await _run(None, _Runtime(_Result(output="still ran")), tmp_path)

    assert outcome.status == "completed"


@pytest.mark.asyncio
async def test_a_store_that_fails_mid_write_does_not_stop_the_stage(tmp_path: Path) -> None:
    class _Broken(_Store):
        def create_job(self, *_a: Any, **_kw: Any) -> None:
            raise OSError("disk went away")

        def update_job(self, *_a: Any, **_kw: Any) -> None:
            raise OSError("disk went away")

    outcome = await _run(_Broken(), _Runtime(_Result(output="still ran")), tmp_path)

    assert outcome.status == "completed"


def test_the_stage_does_not_open_its_own_store() -> None:
    """The store is injected from serve's one storage service. A stage that resolved its own would
    ignore the workspace's storage config and could write jobs to a different backend from the one
    the UI lists (design/details/storage-service.md)."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_pipeline_stage.py"
    ).read_text()

    assert "storage_for_workspace" not in src, "the stage must take the shared store, not build one"
