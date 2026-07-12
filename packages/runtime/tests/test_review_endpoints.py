"""HTTP review-queue endpoints — the shared harness-gate surface (relay/input CLI-cockpit, #2)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.review import FileReviewQueue, ReviewItem

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"


def _seed(ws: Path) -> None:
    q = FileReviewQueue(ws)
    q.submit(
        ReviewItem(
            id="approval-1",
            topology_id="t",
            agent_id="coder",
            skill_id="harness-approval",
            output={"capability": "Bash(npm test)", "rationale": "run tests"},
            verdict={},
            reason="harness requests permission for 'Bash(npm test)'",
            timestamp=datetime.now(tz=UTC),
        )
    )
    q.submit(
        ReviewItem(
            id="input-1",
            topology_id="t",
            agent_id="coder",
            skill_id="harness-input",
            output={"question": "Which cache?", "options": ["redis", "memcached"]},
            verdict={},
            reason="harness needs input: Which cache?",
            timestamp=datetime.now(tz=UTC),
        )
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws)
    _seed(ws)
    app = create_app(ws)
    with TestClient(app) as c:
        yield c


def test_list_pending_surfaces_harness_gates(client: TestClient) -> None:
    items = client.get("/review").json()
    kinds = {i["id"]: i["kind"] for i in items}
    assert kinds["approval-1"] == "permission"
    assert kinds["input-1"] == "input"
    approval = next(i for i in items if i["id"] == "approval-1")
    assert approval["capability"] == "Bash(npm test)"
    inp = next(i for i in items if i["id"] == "input-1")
    assert inp["question"] == "Which cache?" and inp["options"] == ["redis", "memcached"]


def test_approve_permission_gate(client: TestClient) -> None:
    resp = client.post("/review/approval-1/approve").json()
    assert resp["status"] == "approved"
    assert not any(
        i["id"] == "approval-1" for i in client.get("/review").json()
    )  # no longer pending


def test_reject_permission_gate(client: TestClient) -> None:
    assert client.post("/review/approval-1/reject").json()["status"] == "rejected"


def test_answer_input_gate_by_option_index(client: TestClient) -> None:
    resp = client.post("/review/input-1/answer", json={"answer": "0"}).json()
    assert resp["status"] == "approved"
    assert resp["answer"] == "redis"  # index 0 → first option


def test_answer_input_gate_free_text(client: TestClient) -> None:
    resp = client.post("/review/input-1/answer", json={"answer": "use dynamodb"}).json()
    assert resp["answer"] == "use dynamodb"


def test_unknown_item_404(client: TestClient) -> None:
    assert client.get("/review/nope").status_code == 404
