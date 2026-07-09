"""Federated per-run history (GET /instances/{id}/runs) — the "details" lane (design 24).

Aggregates are pushed to the panel; granular per-run cost/status stays on the instance owner's
server and is fetched live, never stored. The route always answers 200 with a reachability envelope
so the UI can distinguish reachable+runs / reachable+empty / poll-mode / unreachable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

_RUNS = [
    {
        "job_id": "j1",
        "topology": "solution-design",
        "status": "completed",
        "created_at": "2026-07-01T10:00:00Z",
        "completed_at": "2026-07-01T10:00:12Z",
        "usage_input_tokens": 1800,
        "usage_output_tokens": 320,
        "usage_cost_usd": 0.042,
    },
    {
        "job_id": "j2",
        "topology": "code-review",
        "status": "failed",
        "created_at": "2026-07-01T09:00:00Z",
        "completed_at": "2026-07-01T09:00:03Z",
        "usage_input_tokens": 500,
        "usage_output_tokens": 40,
        "usage_cost_usd": 0.008,
    },
]


def _client(tmp_path: Path, runs_fn: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if runs_fn is None:

        async def runs_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
            return _RUNS

    return TestClient(create_app(registry, verify=verify, runs=runs_fn))


def _enroll(client: TestClient, connection: str) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def test_direct_instance_returns_per_run_detail_with_cost(tmp_path: Path) -> None:
    seen: list[str] = []

    async def runs_fn(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        seen.append(endpoint)
        return _RUNS

    client = _client(tmp_path, runs_fn)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/runs").json()

    assert body["reachable"] is True and body["reason"] is None
    assert [r["job_id"] for r in body["runs"]] == ["j1", "j2"]
    assert body["runs"][0]["usage_cost_usd"] == 0.042  # per-run cost is what the user wants
    assert seen == ["http://serve:8000"]  # pulled live from the instance, not stored


def test_reachable_but_no_runs_yet(tmp_path: Path) -> None:
    async def empty(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        return []

    client = _client(tmp_path, empty)
    iid = _enroll(client, "direct")
    body = client.get(f"/instances/{iid}/runs").json()
    assert body == {"reachable": True, "reason": None, "runs": []}


def test_poll_mode_instance_is_reachable_false_not_an_error(tmp_path: Path) -> None:
    # A NAT'd Mode-B instance can't be federated; say so honestly (pushed aggregate still shows).
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    resp = client.get(f"/instances/{iid}/runs")
    assert resp.status_code == 200
    assert resp.json() == {"reachable": False, "reason": "poll-mode", "runs": []}


def test_unreachable_instance_reports_unavailable_and_flips_health(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        raise ConnectorError("connection refused")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    resp = client.get(f"/instances/{iid}/runs")
    assert resp.status_code == 200  # not an error path — the UI renders "unavailable"
    assert resp.json() == {"reachable": False, "reason": "unreachable", "runs": []}
    # the failed live-pull also marks the instance unhealthy for the fleet view.
    assert client.get(f"/instances/{iid}").json()["health"] == "unreachable"


def test_unknown_instance_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/instances/nope/runs").status_code == 404
