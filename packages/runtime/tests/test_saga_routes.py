"""Serve pipeline-run (saga) endpoints + the durable-queue signal sink
(design/details/bundled-pipeline-orchestrator.md §4). Serve reads saga state from the shared store
and lazy-reads a node artifact — never importing the controller."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from swarmkit_runtime.artifacts import DatabaseArtifactStore
from swarmkit_runtime.orchestration import SqlSagaStore
from swarmkit_runtime.server._routes_sagas import _register_saga_routes


def _shared_engine() -> object:
    # one shared in-memory connection so the TestClient's threadpool handler sees the same DB
    return create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )


def _app() -> tuple[TestClient, SqlSagaStore, DatabaseArtifactStore]:
    engine = _shared_engine()
    saga_store = SqlSagaStore(engine)
    artifacts = DatabaseArtifactStore(engine)
    app = FastAPI()
    app.state.saga_store = saga_store
    app.state.artifact_store = artifacts
    _register_saga_routes(app)
    return TestClient(app), saga_store, artifacts


def _seed(store: SqlSagaStore, artifacts: DatabaseArtifactStore) -> None:
    a = store.create("run-a", graph_id="sterling-dev", tag="site-42")
    a.passed_stages = ["locate", "build"]
    a.status = "parked"
    a.pending_gate_stage = "review"
    a.artifacts["build"] = artifacts.put("run-a", "build", "the produced diff")
    a.add("parked", stage_id="review")
    store.save(a)
    b = store.create("run-b", graph_id="report-dev", tag="site-17")
    b.status = "completed"
    store.save(b)


def test_list_filter_and_search() -> None:
    client, store, art = _app()
    _seed(store, art)

    assert {s["correlation_id"] for s in client.get("/pipelines/sagas").json()["sagas"]} == {
        "run-a",
        "run-b",
    }
    active = client.get("/pipelines/sagas", params={"status": "active"}).json()["sagas"]
    assert [s["correlation_id"] for s in active] == ["run-a"]
    done = client.get("/pipelines/sagas", params={"status": "completed"}).json()["sagas"]
    assert [s["correlation_id"] for s in done] == ["run-b"]
    found = client.get("/pipelines/sagas", params={"q": "site-42"}).json()["sagas"]
    assert [s["correlation_id"] for s in found] == ["run-a"]


def test_detail_and_lazy_node_artifact() -> None:
    client, store, art = _app()
    _seed(store, art)

    detail = client.get("/pipelines/sagas/run-a").json()
    assert detail["status"] == "parked" and detail["pending_gate_stage"] == "review"
    assert detail["passed_stages"] == ["locate", "build"]
    assert detail["artifacts"]["build"] == "run-a/build/output"
    assert [t["kind"] for t in detail["timeline"]][-1] == "parked"

    node = client.get("/pipelines/sagas/run-a/node/build").json()
    assert node["ref"] == "run-a/build/output" and node["content"] == "the produced diff"
    # a stage with no artifact returns null content, not an error
    assert client.get("/pipelines/sagas/run-a/node/review").json()["content"] is None
    assert client.get("/pipelines/sagas/nope").status_code == 404


def test_signal_enqueues_to_the_store() -> None:
    # the store-backed sink: /pipelines/signal → saga_store.enqueue → the durable queue
    engine = create_engine("sqlite:///:memory:")
    store = SqlSagaStore(engine)
    store.enqueue("c1", '{"kind":"start","graph":"g"}')
    claimed = store.claim("w1")
    assert claimed is not None and claimed[1] == "c1"


def test_404_when_no_store() -> None:
    app = FastAPI()
    app.state.saga_store = None
    _register_saga_routes(app)
    assert TestClient(app).get("/pipelines/sagas").status_code == 404
