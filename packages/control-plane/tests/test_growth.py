"""Tests for growth-loop automation (doc 17): signal → surface → propose → test.

Gap signals push like any aggregation record; GET /gaps ranks them across the fleet;
POST /gaps/propose drives the authoring swarm to draft a fix, runs an eval on it, and
lands a *pending* proposal (the human gate is never bypassed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError

_DRAFT = '{"kind": "skill", "id": "pdf-extract", "content": {"category": "capability"}}'


def _client(tmp_path: Path, author_fn: Any = None, eval_fn: Any = None, **kw: Any) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if author_fn is None:

        async def author_fn(
            endpoint: str, token_ref: str, topology: str, message: str
        ) -> dict[str, Any]:
            return {"reply": f"Draft: {_DRAFT}", "status": "completed"}

    if eval_fn is None:

        async def eval_fn(
            endpoint: str, token_ref: str, eval_topology: str, payload: str
        ) -> dict[str, Any]:
            return {"passed": 9, "total": 10, "pass_rate": 0.9, "status": "completed"}

    return TestClient(create_app(registry, verify=verify, author=author_fn, eval_run=eval_fn, **kw))


def _enroll(client: TestClient, connection: str = "direct") -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
        ).json()["id"]
    )


def _push_gap(client: TestClient, iid: str, capability: str, rid: str) -> None:
    client.post(
        "/aggregate/gap",
        json={
            "instance_id": iid,
            "records": [{"id": rid, "capability": capability, "description": f"need {capability}"}],
        },
    )


def test_gaps_ranked_across_the_fleet(tmp_path: Path) -> None:
    client = _client(tmp_path)
    a, b = _enroll(client), _enroll(client)
    # "pdf-extract" recurs on both instances; "ocr" once.
    _push_gap(client, a, "pdf-extract", "g1")
    _push_gap(client, a, "pdf-extract", "g2")
    _push_gap(client, b, "pdf-extract", "g3")
    _push_gap(client, b, "ocr", "g4")
    gaps = client.get("/gaps").json()
    assert gaps[0]["capability"] == "pdf-extract"
    assert gaps[0]["occurrences"] == 3
    assert gaps[0]["instances"] == 2  # cross-instance spread
    assert gaps[1]["capability"] == "ocr"


def test_propose_from_gap_drafts_evaluates_and_queues(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    resp = client.post("/gaps/propose", json={"instance_id": iid, "capability": "pdf-extract"})
    assert resp.status_code == 200
    prop = resp.json()
    assert prop["status"] == "pending"  # human gate intact — never auto-approved
    assert prop["kind"] == "skill"
    assert prop["artifact_id"] == "pdf-extract"
    assert prop["proposed_by"] == "authoring-swarm"
    assert prop["signal"] == "gap:pdf-extract"
    assert prop["eval_summary"]["pass_rate"] == 0.9  # the test stage ran
    # It shows up in the approval queue.
    assert len(client.get("/proposals?status=pending").json()) == 1


def test_propose_survives_a_failing_eval(tmp_path: Path) -> None:
    async def eval_down(
        endpoint: str, token_ref: str, eval_topology: str, payload: str
    ) -> dict[str, Any]:
        return {"status": "no-eval-topology"}

    client = _client(tmp_path, eval_fn=eval_down)
    iid = _enroll(client)
    prop = client.post("/gaps/propose", json={"instance_id": iid, "capability": "x"}).json()
    # A missing/failed eval must not block the proposal — it lands with the eval status.
    assert prop["status"] == "pending"
    assert prop["eval_summary"]["status"] == "no-eval-topology"


def test_propose_no_draftable_artifact_is_422(tmp_path: Path) -> None:
    async def chatty(endpoint: str, token_ref: str, topology: str, message: str) -> dict[str, Any]:
        return {"reply": "Sure, tell me more about what you need.", "status": "completed"}

    client = _client(tmp_path, author_fn=chatty)
    iid = _enroll(client)
    resp = client.post("/gaps/propose", json={"instance_id": iid, "capability": "x"})
    assert resp.status_code == 422


def test_propose_poll_instance_is_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    assert (
        client.post("/gaps/propose", json={"instance_id": iid, "capability": "x"}).status_code
        == 409
    )


def test_propose_authoring_unreachable_is_502(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str, topology: str, message: str) -> dict[str, Any]:
        raise ConnectorError("run failed")

    client = _client(tmp_path, author_fn=boom)
    iid = _enroll(client)
    assert (
        client.post("/gaps/propose", json={"instance_id": iid, "capability": "x"}).status_code
        == 502
    )
