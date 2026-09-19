"""Worker fairness: workload-class derivation + enqueue sets class/priority (worker-fairness.md).

The claim-side class filter and priority ordering are covered against Postgres in test_job_queue;
here we cover the class derivation (pure, over stubs) and that the API tier stamps class + priority
on the durable row at enqueue (SQLite).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from swarmkit_runtime.server._services import _label_priority, _workload_class


class _Agent:
    def __init__(self, kind: str, children: list[_Agent] | None = None) -> None:
        self.executor = type("Ex", (), {"kind": kind})()
        self.children = children or []


class _Topo:
    def __init__(self, root: _Agent) -> None:
        self.root = root


def test_workload_class_detects_harness_anywhere_in_the_tree() -> None:
    model_only = _Topo(_Agent("model", [_Agent("model"), _Agent("model")]))
    assert _workload_class(model_only) == "model"

    with_harness_child = _Topo(_Agent("model", [_Agent("model"), _Agent("harness")]))
    assert _workload_class(with_harness_child) == "harness"

    harness_root = _Topo(_Agent("harness"))
    assert _workload_class(harness_root) == "harness"


def test_workload_class_defaults_model_for_stubs_without_executor() -> None:
    assert _workload_class(object()) == "model"  # no .root
    assert _workload_class(_Topo(object())) == "model"  # root has no .executor


def test_label_priority_parses_or_defaults_zero() -> None:
    assert _label_priority({"priority": "7"}) == 7
    assert _label_priority({"priority": "nope"}) == 0
    assert _label_priority({}) == 0
    assert _label_priority(None) == 0


# ---- enqueue stamps class + priority --------------------------------------


@pytest.mark.asyncio
async def test_enqueue_stamps_class_and_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
        "governance: {provider: mock}\n"
    )
    (tmp_path / "topologies" / "hello.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: hello, version: 0.1.0}\n"
        "agents:\n  root: {id: g, role: root, model: {provider: mock, name: m},"
        " prompt: {system: hi}}\n"
    )
    app = create_app(tmp_path, enqueue_only=True)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        r = await client.post("/run/hello", json={"input": "x", "labels": {"priority": "5"}})
        job_id = r.json()["job_id"]
    row: Any = app.state.store.get_job(job_id)
    assert row.job_class == "model"  # a model-executor topology
    assert row.priority == 5
