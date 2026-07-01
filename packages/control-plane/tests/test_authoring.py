"""Tests for the conversational-authoring proxy (POST /instances/{id}/author).

The panel drives an instance's authoring swarm (Mode A) and returns its reply plus any
drafted artifact ({kind, id, content}) the swarm emitted, so the UI can preview it and
propose it for approval. Operator-only; Mode B → 409.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError


def _client(tmp_path: Path, author_fn: Any = None, **kw: Any) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        return {}

    if author_fn is None:

        async def author_fn(
            endpoint: str, token_ref: str, topology: str, message: str
        ) -> dict[str, Any]:
            return {"reply": "Here's a draft.", "status": "completed"}

    return TestClient(create_app(registry, verify=verify, author=author_fn, **kw))


def _enroll(client: TestClient, connection: str, **hdr: Any) -> str:
    return str(
        client.post(
            "/instances",
            json={"name": "x", "endpoint": "http://serve:8000", "connection": connection},
            **hdr,
        ).json()["id"]
    )


def test_author_turn_returns_reply(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    async def author_fn(
        endpoint: str, token_ref: str, topology: str, message: str
    ) -> dict[str, Any]:
        seen.update(endpoint=endpoint, topology=topology, message=message)
        return {"reply": "A greeter topology, roughly.", "status": "completed"}

    client = _client(tmp_path, author_fn)
    iid = _enroll(client, "direct")
    resp = client.post(f"/instances/{iid}/author", json={"message": "build a greeter"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "A greeter topology, roughly."
    assert body["artifact"] is None  # plain conversation → nothing to propose
    assert seen["endpoint"] == "http://serve:8000"
    assert seen["topology"] == "authoring"  # default topology
    assert seen["message"] == "build a greeter"


def test_author_turn_extracts_drafted_artifact(tmp_path: Path) -> None:
    draft = '{"kind": "topology", "id": "greeter", "content": {"nodes": []}}'

    async def author_fn(
        endpoint: str, token_ref: str, topology: str, message: str
    ) -> dict[str, Any]:
        return {"reply": f"Done. {draft}", "status": "completed"}

    client = _client(tmp_path, author_fn)
    iid = _enroll(client, "direct")
    art = client.post(f"/instances/{iid}/author", json={"message": "greeter"}).json()["artifact"]
    assert art == {"kind": "topology", "id": "greeter", "content": {"nodes": []}}


def test_author_poll_instance_is_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, "poll")
    resp = client.post(f"/instances/{iid}/author", json={"message": "hi"})
    assert resp.status_code == 409  # driving a swarm needs a directly-reachable instance


def test_author_unknown_instance_404(tmp_path: Path) -> None:
    resp = _client(tmp_path).post("/instances/nope/author", json={"message": "hi"})
    assert resp.status_code == 404


def test_author_unreachable_is_502(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str, topology: str, message: str) -> dict[str, Any]:
        raise ConnectorError("run failed")

    client = _client(tmp_path, boom)
    iid = _enroll(client, "direct")
    assert client.post(f"/instances/{iid}/author", json={"message": "hi"}).status_code == 502


def test_author_is_operator_only_under_auth(tmp_path: Path) -> None:
    # A Mode B connector token must NOT be able to drive authoring (authorize denies it);
    # the operator token can.
    op = {"Authorization": "Bearer op-token"}
    client = _client(tmp_path, operator_tokens=["op-token"])
    iid = _enroll(client, "direct", headers=op)
    minted = client.post(f"/instances/{iid}/mint-token", json={}, headers=op).json()["token"]
    conn = {"Authorization": f"Bearer {minted}"}
    assert (
        client.post(f"/instances/{iid}/author", json={"message": "hi"}, headers=conn).status_code
        == 403
    )
    assert (
        client.post(f"/instances/{iid}/author", json={"message": "hi"}, headers=op).status_code
        == 200
    )
