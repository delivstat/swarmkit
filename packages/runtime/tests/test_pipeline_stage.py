"""The bundled run-stage execution seam (design/details/bundled-pipeline-orchestrator.md, slice 5b):
run a stage's topology, persist its output to the ArtifactStore, and return a reference-only
StageOutcome — ``parked`` when the stage is gated, ``completed`` when it is not, ``failed`` on
error. The first stage is seeded with the pipeline input payload (persisted on the saga); downstream
stages thread the correlation's prior artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.artifacts._backends import DatabaseArtifactStore
from swarmkit_runtime.orchestration import InMemorySagaStore, RunStage, SagaStore
from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage


@dataclass
class _FakeRuntime:
    """Records inputs; echoes a per-topology output (stands in for a WorkspaceRuntime)."""

    seen: list[tuple[str, str]] = field(default_factory=list)
    fail_on: str = ""

    async def run(self, topology: str, user_input: str, **_: object) -> object:
        if topology == self.fail_on:
            raise RuntimeError("boom")
        if topology == "__missing__":
            raise KeyError(topology)
        self.seen.append((topology, user_input))
        return type("R", (), {"output": f"<{topology} output>"})()


def _store() -> DatabaseArtifactStore:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    return DatabaseArtifactStore(engine)


def _seam(
    rt: _FakeRuntime, store: DatabaseArtifactStore, saga_store: SagaStore | None = None
) -> RunStage:
    # The seam takes a concrete WorkspaceRuntime; the fake supplies only the `.run` it needs. An
    # empty saga store (no saga for the correlation) falls back to prior-artifact input.
    return build_pipeline_run_stage(
        cast("WorkspaceRuntime", rt), store, saga_store or InMemorySagaStore()
    )


@pytest.mark.asyncio
async def test_ungated_stage_completes_and_persists_artifact() -> None:
    store, rt = _store(), _FakeRuntime()
    run_stage = _seam(rt, store)

    outcome = await run_stage("run-1", {"id": "intake", "topology": "oms-intake"})

    assert outcome.status == "completed"
    assert outcome.artifact == "run-1/intake/output"
    assert store.get(outcome.artifact) == "<oms-intake output>"


@pytest.mark.asyncio
async def test_gated_stage_parks() -> None:
    store, rt = _store(), _FakeRuntime()
    run_stage = _seam(rt, store)

    outcome = await run_stage("run-1", {"id": "design", "topology": "oms-design", "gate": "g"})

    assert outcome.status == "parked"
    assert store.get(outcome.artifact) == "<oms-design output>"


@pytest.mark.asyncio
async def test_input_is_threaded_from_prior_artifacts() -> None:
    store, rt = _store(), _FakeRuntime()
    run_stage = _seam(rt, store)

    await run_stage("run-1", {"id": "intake", "topology": "oms-intake"})
    await run_stage("run-1", {"id": "design", "topology": "oms-design"})

    # the design stage saw the intake artifact as its input
    assert rt.seen[-1] == ("oms-design", "<oms-intake output>")


@pytest.mark.asyncio
async def test_run_error_is_a_failed_outcome() -> None:
    store = _store()
    run_stage = _seam(_FakeRuntime(fail_on="oms-build"), store)
    outcome = await run_stage("run-1", {"id": "build", "topology": "oms-build"})
    assert outcome.status == "failed" and "RuntimeError" in outcome.detail


@pytest.mark.asyncio
async def test_unknown_topology_is_failed() -> None:
    run_stage = _seam(_FakeRuntime(), _store())
    outcome = await run_stage("run-1", {"id": "x", "topology": "__missing__"})
    assert outcome.status == "failed" and "unknown topology" in outcome.detail


@pytest.mark.asyncio
async def test_stage_without_topology_is_failed() -> None:
    run_stage = _seam(_FakeRuntime(), _store())
    outcome = await run_stage("run-1", {"id": "x"})
    assert outcome.status == "failed"


@pytest.mark.asyncio
async def test_first_stage_is_seeded_with_the_pipeline_input_payload() -> None:
    # The bug fix: a `start` event's input payload reaches the first stage's run (not dropped).
    sagas = InMemorySagaStore()
    sagas.create("run-1", graph_id="g", input="BRD-42: add split shipment")
    rt = _FakeRuntime()
    run_stage = _seam(rt, _store(), sagas)

    await run_stage("run-1", {"id": "intake", "topology": "oms-intake"})

    assert rt.seen[-1] == ("oms-intake", "BRD-42: add split shipment")


@pytest.mark.asyncio
async def test_downstream_stage_uses_upstream_artifact_not_the_input() -> None:
    sagas = InMemorySagaStore()
    saga = sagas.create("run-1", graph_id="g", input="original payload")
    store, rt = _store(), _FakeRuntime()
    run_stage = _seam(rt, store, sagas)

    # intake runs on the payload, produces an artifact, and is marked passed
    await run_stage("run-1", {"id": "intake", "topology": "oms-intake"})
    saga.passed_stages.append("intake")
    sagas.save(saga)

    # design (a downstream stage) now threads intake's artifact, not the original payload
    await run_stage("run-1", {"id": "design", "topology": "oms-design"})
    assert rt.seen[-1] == ("oms-design", "<oms-intake output>")
