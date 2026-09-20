"""A caller can see their own connections and nobody else's.

`GET /api/oauth/credentials` answers "who has connected what". With one operator that is an
inventory; in a multi-user deployment it is a roster, and in some organisations that list is more
sensitive than any single connection. It is therefore admin-only, and a caller's own state lives on
a different route.

Two routes rather than one filtered by the frontend, because a filtered page has already *received*
what it declines to draw. The point of `my-credentials` is not that it is permitted to see only the
caller's rows — it is that it cannot express any other query: no owner parameter, no filter, nothing
to tamper with. `test_naming_another_owner_changes_nothing` is what makes that claim testable rather
than aspirational.

See design/details/per-caller-credential-delegation.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import NoneAuthProvider
from swarmkit_runtime.oauth import TokenStore
from swarmkit_runtime.server import _required_action
from swarmkit_runtime.server._app import create_app

_TOKEN: dict[str, Any] = {
    "access_token": "secret-bytes",
    "refresh_token": "r",
    "expires_in": 9999,
    "scope": "calendar.read",
}


def _workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "team", "name": "Team"},
                "credentials": {
                    "github": {"source": "env", "config": {"env": "GITHUB_TOKEN"}},
                    "calendar": {
                        "source": "oauth",
                        "identity": "per-user",
                        "config": {"endpoint": "https://stub.example/mcp"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "topologies").mkdir(exist_ok=True)
    return tmp_path


def _client(tmp_path: Path, who: str) -> TestClient:
    return TestClient(
        create_app(_workspace(tmp_path), auth_provider=NoneAuthProvider(identity=who))
    )


def _connect(tmp_path: Path, owner: str) -> None:
    TokenStore(tmp_path).save(
        credential_id="calendar",
        owner=owner,
        provider="stub",
        endpoint="https://stub.example/mcp",
        token_response=_TOKEN,
    )


# --- the roster ------------------------------------------------------------------------------


def test_the_operator_inventory_is_admin_only() -> None:
    """The listing that names owners requires admin; a caller's own state does not."""
    assert _required_action("GET", "/api/oauth/credentials") == "admin"
    assert _required_action("GET", "/api/oauth/my-credentials") == "read"


def test_disconnecting_yourself_is_not_an_administrative_act() -> None:
    """The handler is owner-scoped, so this can only ever remove the caller's own token."""
    assert _required_action("DELETE", "/api/oauth/credentials/calendar") == "run"


def test_my_credentials_shows_only_the_callers_own_connection(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _workspace(ws)
    _connect(ws, "bob")

    with _client(ws, "alice") as client:
        alice = client.get("/api/oauth/my-credentials").json()
    with _client(ws, "bob") as client:
        bob = client.get("/api/oauth/my-credentials").json()

    def calendar(payload: dict[str, Any]) -> dict[str, Any]:
        return next(c for c in payload["credentials"] if c["credential_id"] == "calendar")

    assert calendar(alice)["connected"] is False
    assert calendar(bob)["connected"] is True
    # Alice is told nothing whatsoever about Bob.
    assert "bob" not in str(alice)


def test_naming_another_owner_changes_nothing(tmp_path: Path) -> None:
    """There is no owner parameter, so asking for one is not refused — it is impossible.

    Asserted against every shape an attempt could take, because "we do not read that parameter
    today" is exactly the kind of claim that stops being true after a refactor.
    """
    ws = tmp_path / "ws"
    _workspace(ws)
    _connect(ws, "bob")

    with _client(ws, "alice") as client:
        plain = client.get("/api/oauth/my-credentials").json()
        with_query = client.get("/api/oauth/my-credentials?owner=bob").json()
        with_header = client.get(
            "/api/oauth/my-credentials", headers={"X-Owner": "bob", "X-Swarmkit-Owner": "bob"}
        ).json()

    assert with_query == plain
    assert with_header == plain
    assert plain["owner"] == "alice"
    assert "secret-bytes" not in str(plain)


# --- what the page needs to render ------------------------------------------------------------


def test_a_global_connection_carries_no_personal_state(tmp_path: Path) -> None:
    """`connected: false` on a global row would be a lie — that token is not theirs to hold."""
    ws = tmp_path / "ws"
    _workspace(ws)

    with _client(ws, "alice") as client:
        rows = client.get("/api/oauth/my-credentials").json()["credentials"]

    github = next(c for c in rows if c["credential_id"] == "github")
    assert github["identity"] == "global"
    assert github["connected"] is None


def test_declared_connections_are_listed_before_anyone_connects(tmp_path: Path) -> None:
    """The page has to show what the agent will use as you, or there is nothing to click."""
    ws = tmp_path / "ws"
    _workspace(ws)

    with _client(ws, "alice") as client:
        payload = client.get("/api/oauth/my-credentials").json()

    assert {c["credential_id"] for c in payload["credentials"]} == {"github", "calendar"}


def test_no_route_returns_a_token(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _workspace(ws)
    _connect(ws, "alice")

    with _client(ws, "alice") as client:
        body = client.get("/api/oauth/my-credentials").text

    assert "secret-bytes" not in body


@pytest.mark.parametrize("field", ["scopes", "expires_at", "expired"])
def test_a_connected_row_carries_enough_to_warn_about_expiry(tmp_path: Path, field: str) -> None:
    ws = tmp_path / "ws"
    _workspace(ws)
    _connect(ws, "alice")

    with _client(ws, "alice") as client:
        rows = client.get("/api/oauth/my-credentials").json()["credentials"]

    calendar = next(c for c in rows if c["credential_id"] == "calendar")
    assert calendar["connected"] is True
    assert field in calendar
