"""What a fleet pulls on sync besides state: the gap log and the audit tail — and the kinds a
fleet state now names (design/details/control-plane/27-fleet-signals-and-kinds.md).

`GET /gaps` is what `swarmkit gaps` prints; `GET /audit?since=` is the incremental read a sync
cursors on; `/fleet/state` carries funnels, contracts and role registries next to the four kinds
it always had, each with content and text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import copy_workspace
from fastapi.testclient import TestClient
from swarmkit_runtime.gaps import SkillGap, SkillGapLog
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

_FUNNEL = """\
apiVersion: swarmkit/v1
kind: Funnel
metadata: {id: design-gate, name: Design gate, description: One lead signs off.}
approve:
  rules:
    - scope: design:approve
      roles: [engineering-lead]
      quorum: all
provenance: {authored_by: human, version: 1.0.0}
"""
_CONTRACT = """\
apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: handbook-finance
  name: Handbook ↔ Finance
  description: The expense rules.   # the lock's vocabulary
parties: [handbook, finance-portal]
provenance: {authored_by: human, version: 1.0.0}
"""
_ROLES = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata: {id: leads, name: Approval leads}
roles:
  - id: engineering-lead
    members: [alice]
    scopes: [design:approve]
"""


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    ws = copy_workspace(EXAMPLE_WS, tmp_path / "ws")
    for d, name, text in (
        ("funnels", "design-gate.yaml", _FUNNEL),
        ("contracts", "handbook-finance.yaml", _CONTRACT),
        ("roles", "leads.yaml", _ROLES),
    ):
        (ws / d).mkdir(exist_ok=True)
        (ws / d / name).write_text(text)
    return ws


@pytest.fixture()
def client(workspace: Path) -> Iterator[TestClient]:
    with TestClient(create_app(workspace)) as c:
        yield c


def test_fleet_state_names_funnels_contracts_and_roles(client: TestClient) -> None:
    arts = client.get("/fleet/state").json()["artifacts"]
    assert [e["id"] for e in arts["funnels"]] == ["design-gate"]
    assert [e["id"] for e in arts["contracts"]] == ["handbook-finance"]
    assert [e["id"] for e in arts["roles"]] == ["leads"]
    contract = arts["contracts"][0]
    assert contract["content"]["parties"] == ["handbook", "finance-portal"]
    assert "# the lock's vocabulary" in contract["yaml"]
    assert contract["version"] == "1.0.0"
    manifest = client.get("/fleet/state/manifest").json()["artifacts"]
    assert set(manifest["roles"][0]) == {"id", "version", "content_hash"}


def test_gaps_lists_the_gap_log(client: TestClient, workspace: Path) -> None:
    assert client.get("/gaps").json() == []
    log = SkillGapLog(workspace)
    gap = SkillGap(
        skill_id="translate-text",
        topology_id="hello",
        pattern="agent 'root' called a tool it does not hold",
        suggested_action="author a 'translate-text' skill",
        first_seen=datetime.now(UTC),
    )
    log.record(gap)
    log.record(gap)
    rows = client.get("/gaps").json()
    assert len(rows) == 1
    assert rows[0]["skill_id"] == "translate-text"
    assert rows[0]["topology_id"] == "hello"
    assert rows[0]["occurrences"] == 2
    assert rows[0]["suggested_action"].startswith("author")
    datetime.fromisoformat(rows[0]["first_seen"])  # ISO 8601


def test_audit_since_returns_only_newer_events(client: TestClient) -> None:
    provider = client.app.state.runtime.audit_provider  # type: ignore[attr-defined]
    old = datetime.now(UTC) - timedelta(hours=2)
    new = datetime.now(UTC)

    async def seed() -> None:
        await provider.record(
            AuditEvent(
                event_type="skill.gap", agent_id="a", timestamp=old, payload={}, event_id=uuid4()
            )
        )
        await provider.record(
            AuditEvent(
                event_type="run.completed",
                agent_id="a",
                timestamp=new,
                payload={},
                event_id=uuid4(),
            )
        )

    asyncio.run(seed())
    every = {e["event_type"] for e in client.get("/audit").json()}
    assert {"skill.gap", "run.completed"} <= every
    cursor = (new - timedelta(minutes=1)).isoformat()
    newer = [e["event_type"] for e in client.get("/audit", params={"since": cursor}).json()]
    assert "run.completed" in newer
    assert "skill.gap" not in newer
