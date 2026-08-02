"""Resolving a multi-party approval role-task — the identity model.

Slice 1 of design/details/pipeline-gate-approval-ui.md: the resolver is the *authenticated caller*,
not a request-body field, gated on the reserved `approvals:resolve` scope (§8.7) and checked against
the workspace role registry. Covers the HTTP surface and the `swarmkit review resolve` CLI.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import copy_workspace
from fastapi.testclient import TestClient
from swarmkit_runtime.auth._scopes import RESERVED_SCOPES, reserved_violations
from swarmkit_runtime.governance._approval import Role, RoleRegistry
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.review._multiparty import membership_error

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

ROLES_YAML = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: test-roles
  name: Test roles
roles:
  - id: security-reviewer
    members: [alice]
    scopes: [security:approve]
  - id: release-manager
    members: [bob]
    scopes: [security:approve]
"""


def _role_task(item_id: str, role: str) -> ReviewItem:
    return ReviewItem(
        id=item_id,
        topology_id="run-42",
        agent_id="design",
        skill_id="multi-party-approval",
        output={
            "gate_id": "run-42:design",
            "scope": "security:approve",
            "role": role,
            "rule_index": 0,
        },
        verdict={},
        reason=f"multi-party approval: {role} must approve security:approve",
        timestamp=datetime.now(tz=UTC),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    (ws / "roles").mkdir(exist_ok=True)
    (ws / "roles" / "test-roles.yaml").write_text(ROLES_YAML)
    queue = FileReviewQueue(ws)
    queue.submit(_role_task("mpa-run-42:design-0-security-reviewer", "security-reviewer"))
    queue.submit(_role_task("mpa-run-42:design-0-release-manager", "release-manager"))
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
    return ws


@pytest.fixture
def client(workspace: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(workspace)
    with TestClient(app) as c:
        yield c


# ---- the reserved scope --------------------------------------------------


def test_approvals_resolve_is_reserved_for_humans() -> None:
    """A transport token must never be able to carry it (§8.7)."""
    assert "approvals:resolve" in RESERVED_SCOPES
    assert reserved_violations(frozenset({"serve:run", "approvals:resolve"})) == frozenset(
        {"approvals:resolve"}
    )


# ---- membership, the registry-only subset of resolution_error ------------


def _registry() -> RoleRegistry:
    return RoleRegistry(
        roles={
            "security-reviewer": Role(
                id="security-reviewer",
                members=frozenset({"alice"}),
                scopes=frozenset({"security:approve"}),
            )
        }
    )


def test_membership_error_accepts_a_member() -> None:
    assert (
        membership_error(
            _registry(), role="security-reviewer", scope="security:approve", identity="alice"
        )
        is None
    )


@pytest.mark.parametrize(
    ("role", "scope", "identity", "fragment"),
    [
        ("ghost", "security:approve", "alice", "unknown role"),
        ("security-reviewer", "release:approve", "alice", "does not confer"),
        ("security-reviewer", "security:approve", "mallory", "not a member"),
    ],
)
def test_membership_error_reports_the_specific_reason(
    role: str, scope: str, identity: str, fragment: str
) -> None:
    err = membership_error(_registry(), role=role, scope=scope, identity=identity)
    assert err is not None and fragment in err


# ---- the HTTP surface ----------------------------------------------------


def test_resolve_body_carries_no_identity(client: TestClient) -> None:
    """A body-supplied identity is not merely ignored — the schema rejects it as unknown input
    only if extra fields are forbidden; what matters is that it never reaches record_resolution."""
    resp = client.post(
        "/review/mpa-run-42:design-0-security-reviewer/resolve",
        json={"outcome": "approve", "identity": "mallory"},
    )
    assert resp.status_code == 403, resp.text
    # anonymous (the NoneAuthProvider default) is not a member — the body's "mallory" is irrelevant
    assert "anonymous" in resp.json()["detail"]
    assert "mallory" not in resp.json()["detail"]


def test_resolve_rejects_a_non_member_caller(client: TestClient) -> None:
    resp = client.post(
        "/review/mpa-run-42:design-0-security-reviewer/resolve", json={"outcome": "approve"}
    )
    assert resp.status_code == 403
    assert "not a member of role security-reviewer" in resp.json()["detail"]


def test_resolve_rejects_a_non_role_task_item(client: TestClient) -> None:
    resp = client.post("/review/approval-1/resolve", json={"outcome": "approve"})
    assert resp.status_code == 403
    assert "not a multi-party approval role-task" in resp.json()["detail"]


def test_resolve_accepts_a_member_caller(workspace: Path) -> None:
    """With the anonymous identity listed as a role member, the resolution lands and records the
    caller — the local-dev configuration the design note sanctions."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    (workspace / "roles" / "test-roles.yaml").write_text(
        ROLES_YAML.replace("members: [alice]", "members: [alice, anonymous]")
    )
    with TestClient(create_app(workspace)) as c:
        resp = c.post(
            "/review/mpa-run-42:design-0-security-reviewer/resolve", json={"outcome": "approve"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

    item = FileReviewQueue(workspace).get("mpa-run-42:design-0-security-reviewer")
    assert item is not None
    assert item.answer == "anonymous"  # the authenticated caller, not a body field


def test_resolve_audits_both_the_allow_and_the_deny_path(workspace: Path) -> None:
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    (workspace / "roles" / "test-roles.yaml").write_text(
        ROLES_YAML.replace("members: [alice]", "members: [alice, anonymous]")
    )
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"approvals:resolve"}))
    with TestClient(create_app(workspace)) as c:
        c.app.state.runtime._governance = gov  # type: ignore[attr-defined]
        c.post("/review/mpa-run-42:design-0-security-reviewer/resolve", json={"outcome": "approve"})
        # release-manager does not list anonymous -> denied on membership
        c.post("/review/mpa-run-42:design-0-release-manager/resolve", json={"outcome": "approve"})

    events = [e for e in gov.events if e.event_type == "approval.role_task_resolved"]
    assert len(events) == 2, [e.event_type for e in gov.events]
    assert sorted(str(e.policy_decision) for e in events) == ["allow", "deny"]
    denied = next(e for e in events if e.policy_decision == "deny")
    assert "not a member of role release-manager" in str(denied.policy_reason)


def test_resolve_denied_without_the_reserved_scope(workspace: Path) -> None:
    """Membership alone is not enough — the caller must also hold `approvals:resolve`, so an
    agent-tier identity can never cast a resolution (§8.7)."""
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    (workspace / "roles" / "test-roles.yaml").write_text(
        ROLES_YAML.replace("members: [alice]", "members: [alice, anonymous]")
    )
    gov = MockGovernanceProvider()  # no allowed_scopes -> approvals:resolve denied
    with TestClient(create_app(workspace)) as c:
        c.app.state.runtime._governance = gov  # type: ignore[attr-defined]
        resp = c.post(
            "/review/mpa-run-42:design-0-security-reviewer/resolve", json={"outcome": "approve"}
        )

    assert resp.status_code == 403, resp.text
    item = FileReviewQueue(workspace).get("mpa-run-42:design-0-security-reviewer")
    assert item is not None and item.status == "pending"
    assert [
        e.policy_decision for e in gov.events if e.event_type == "approval.role_task_resolved"
    ] == ["deny"]


# ---- the CLI -------------------------------------------------------------


def test_cli_resolve_records_the_asserted_identity(workspace: Path) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    result = CliRunner().invoke(
        app,
        [
            "review",
            "resolve",
            "mpa-run-42:design-0-security-reviewer",
            "--as",
            "alice",
            "--approve",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    item = FileReviewQueue(workspace).get("mpa-run-42:design-0-security-reviewer")
    assert item is not None
    assert item.status == "approved" and item.answer == "alice"


def test_cli_resolve_refuses_a_non_member(workspace: Path) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    result = CliRunner().invoke(
        app,
        [
            "review",
            "resolve",
            "mpa-run-42:design-0-security-reviewer",
            "--as",
            "mallory",
            str(workspace),
        ],
    )
    assert result.exit_code != 0
    item = FileReviewQueue(workspace).get("mpa-run-42:design-0-security-reviewer")
    assert item is not None and item.status == "pending"


def test_cli_resolve_refuses_a_harness_gate(workspace: Path) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    result = CliRunner().invoke(
        app, ["review", "resolve", "approval-1", "--as", "alice", str(workspace)]
    )
    assert result.exit_code != 0


# ---- /whoami -------------------------------------------------------------


def test_whoami_reports_the_authenticated_caller(client: TestClient) -> None:
    """A front-end resolving a role-task needs to say which capacity it is acting in; /auth-info is
    public and describes the server, not the caller."""
    body = client.get("/whoami").json()
    assert body["client_id"] == "anonymous"  # NoneAuthProvider default
    assert body["mode"] == "none"
    assert isinstance(body["scopes"], list)
