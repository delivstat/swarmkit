"""Federated per-run trace (GET /instances/{id}/runs/{run}/trace) — the fleet run graph's data
source (fleet-run-graph.md, task #25).

Same "details" lane as /runs (design 24): the span tree stays on the owner instance and is fetched
live, never stored. The route always answers 200 with a ``{reachable, reason, trace}`` envelope so
the UI can tell apart trace / no-trace / poll-mode / unreachable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

# A minimal span tree, shaped exactly as the instance's /observability/runs/<id>/trace returns.
_TRACE = {
    "name": "topology.run",
    "start_ns": 0,
    "end_ns": 1000,
    "duration_ms": 1.0,
    "attributes": {"agent.id": "root"},
    "children": [
        {
            "name": "agent.step.root",
            "start_ns": 10,
            "end_ns": 900,
            "duration_ms": 0.9,
            "attributes": {"agent.id": "root", "cost.usd": 0.03},
            "children": [],
        }
    ],
}


def _client(tmp_path: Path, trace_fn: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if trace_fn is None:

        async def trace_fn(endpoint: str, token_ref: str, run_id: str) -> dict[str, Any] | None:
            return _TRACE

    return TestClient(create_app(registry, verify=verify, run_trace=trace_fn))


def _enroll(client: TestClient, connection: str) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_direct_instance_returns_the_span_tree(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    async def trace_fn(endpoint: str, token_ref: str, run_id: str) -> dict[str, Any] | None:
        seen.append((endpoint, run_id))
        return _TRACE

    client = _client(tmp_path, trace_fn)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/runs/j1/trace").json()

    assert body["reachable"] is True and body["reason"] is None
    assert body["trace"]["name"] == "topology.run"
    assert body["trace"]["children"][0]["attributes"]["cost.usd"] == 0.03
    assert seen == [("http://serve:8000", "j1")]  # pulled live for that run id, not stored


def test_reachable_but_no_trace_for_that_run(tmp_path: Path) -> None:
    # The instance answered 404 (no trace recorded) — reachable, but nothing to show.
    async def none(endpoint: str, token_ref: str, run_id: str) -> dict[str, Any] | None:
        return None

    client = _client(tmp_path, none)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/runs/j9/trace").json()
    assert body == {"reachable": True, "reason": "no-trace", "trace": None}


def test_poll_mode_instance_is_reachable_false_not_an_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    resp = client.get(f"/instances/{iid}/runs/j1/trace")
    assert resp.status_code == 200
    assert resp.json() == {"reachable": False, "reason": "poll-mode", "trace": None}


def test_unreachable_instance_reports_unavailable_and_flips_health(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str, run_id: str) -> dict[str, Any] | None:
        raise ConnectorError("connection refused")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    resp = client.get(f"/instances/{iid}/runs/j1/trace")
    assert resp.status_code == 200
    assert resp.json() == {"reachable": False, "reason": "unreachable", "trace": None}
    assert client.get(f"/instances/{iid}").json()["health"] == "unreachable"


def test_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/runs/j1/trace").status_code == 404
