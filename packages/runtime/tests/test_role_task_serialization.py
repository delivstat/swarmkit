"""Role-task serialization + gate detail (slice 2 of pipeline-gate-approval-ui.md).

A multi-party role-task used to serialize as ``kind: "other"`` with its gate, role and scope
dropped, so no front-end could render or group one. This covers the fourth kind, the review-queue
filters and the fourth kind. The per-role gate detail this also covered moved to
``GET /gates/{gate_id}`` (1.181.0), which resolves the policy from the gate's own funnel and applies
quorum through the approval engine — see `test_gate_state.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import copy_workspace
from fastapi.testclient import TestClient
from swarmkit_runtime.review import FileReviewQueue, ReviewItem

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

GATE = "run-42:greeter"


def _role_task(role: str, *, status: str = "pending", answer: str = "") -> ReviewItem:
    return ReviewItem(
        id=f"mpa-{GATE}-0-{role}",
        topology_id="run-42",
        agent_id="greeter",
        skill_id="multi-party-approval",
        output={"gate_id": GATE, "role": role, "scope": "greet:approve", "rule_index": 0},
        verdict={},
        reason=f"role {role!r} must approve",
        timestamp=datetime.now(tz=UTC),
        status=status,  # type: ignore[arg-type]
        answer=answer,
    )


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    dest = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, dest)
    queue = FileReviewQueue(dest)
    queue.submit(_role_task("security-reviewer"))
    queue.submit(_role_task("release-manager"))
    queue.submit(
        ReviewItem(
            id="approval-1",
            topology_id="t",
            agent_id="coder",
            skill_id="harness-approval",
            output={"capability": "Bash(npm test)"},
            verdict={},
            reason="harness requests permission",
            timestamp=datetime.now(tz=UTC),
        )
    )
    return dest


@pytest.fixture
def client(ws: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as c:
        yield c


# ---- the fourth kind -----------------------------------------------------


def test_role_task_serializes_with_its_gate_role_and_scope(client: TestClient) -> None:
    items = {i["id"]: i for i in client.get("/review").json()}
    task = items[f"mpa-{GATE}-0-security-reviewer"]
    assert task["kind"] == "role_task"
    assert task["gate_id"] == GATE
    assert task["role"] == "security-reviewer"
    assert task["scope"] == "greet:approve"
    assert task["rule_index"] == 0
    assert task["resolved_by"] == ""  # pending


def test_harness_kinds_are_unchanged(client: TestClient) -> None:
    """The new branch must not reclassify the two kinds a front-end already renders."""
    items = {i["id"]: i for i in client.get("/review").json()}
    assert items["approval-1"]["kind"] == "permission"
    assert items["approval-1"]["capability"] == "Bash(npm test)"
    assert items["approval-1"]["resolved_by"] == ""  # only meaningful for a role-task


def test_resolved_by_carries_the_resolver(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    FileReviewQueue(ws).record_resolution(f"mpa-{GATE}-0-security-reviewer", "approved", "alice")
    with TestClient(create_app(ws)) as c:
        item = c.get(f"/review/mpa-{GATE}-0-security-reviewer").json()
    assert item["status"] == "approved"
    assert item["resolved_by"] == "alice"


# ---- filters -------------------------------------------------------------


def test_review_filters_by_kind(client: TestClient) -> None:
    ids = {i["id"] for i in client.get("/review", params={"kind": "role_task"}).json()}
    assert ids == {f"mpa-{GATE}-0-security-reviewer", f"mpa-{GATE}-0-release-manager"}
    assert [i["id"] for i in client.get("/review", params={"kind": "permission"}).json()] == [
        "approval-1"
    ]


def test_review_filters_by_gate(client: TestClient) -> None:
    same = client.get("/review", params={"gate_id": GATE}).json()
    assert len(same) == 2
    assert client.get("/review", params={"gate_id": "run-99:other"}).json() == []


def test_review_all_takes_the_same_filters(client: TestClient) -> None:
    assert len(client.get("/review/all", params={"kind": "role_task"}).json()) == 2


# ---- gate detail + the quorum correction ---------------------------------
