"""POST /api/skills/{id}/activate — preflight check for runnability.

See design/details/skill-requires-credentials.md §Activation refusal. The route itself changes
no state; it answers "can this caller run this skill right now?" by looking up each
`requires_credentials` entry against the caller's own `oauth_tokens` row. A 409 names what the
caller needs to connect, and (for providers whose issuer is in `SETUP_TOPOLOGIES`) which setup
topology will walk them through registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import NoneAuthProvider
from swarmkit_runtime.oauth import KEY_ENV, TokenStore
from swarmkit_runtime.server._app import create_app

_TOKEN: dict[str, Any] = {
    "access_token": "a",
    "refresh_token": "r",
    "expires_in": 3600,
    "scope": "x",
}


def _ws(root: Path, *, requires_credentials: list[str] | None = None) -> Path:
    """A minimal workspace with a Gmail server + one mcp_tool skill.

    `requires_credentials` controls whether the skill carries the explicit field (the primary
    path) or relies on the server-prefix fallback (the inferred path).
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "topologies").mkdir(exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    data: dict[str, Any] = {
        "apiVersion": "swarmkit/v1",
        "kind": "Workspace",
        "metadata": {"id": "t", "name": "T"},
        "credentials": {
            "gmail": {
                "source": "oauth",
                "identity": "per-user",
                "config": {"endpoint": "https://gmailmcp.googleapis.com/mcp/v1"},
            }
        },
        "mcp_servers": [
            {
                "id": "gmail",
                "transport": "http",
                "endpoint": "https://gmailmcp.googleapis.com/mcp/v1",
                "credentials_ref": "gmail",
                "permission": "readonly",
            }
        ],
    }
    (root / "workspace.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    skill: dict[str, Any] = {
        "apiVersion": "swarmkit/v1",
        "kind": "Skill",
        "metadata": {
            "id": "gmail-search",
            "name": "Gmail Search",
            "description": "Searches Gmail threads via the gmail server.",
        },
        "category": "capability",
        "implementation": {
            "type": "mcp_tool",
            "server": "gmail",
            "tool": "search_threads",
        },
        "provenance": {"authored_by": "human", "version": "1.0.0"},
    }
    if requires_credentials is not None:
        skill["requires_credentials"] = requires_credentials
    (root / "skills" / "gmail-search.yaml").write_text(yaml.safe_dump(skill), encoding="utf-8")
    return root


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())


def _client(ws: Path, identity: str) -> TestClient:
    return TestClient(create_app(ws, auth_provider=NoneAuthProvider(identity=identity)))


def _connect(ws: Path, owner: str) -> None:
    TokenStore(ws).save(
        credential_id="gmail",
        owner=owner,
        provider="google",
        endpoint="https://gmailmcp.googleapis.com/mcp/v1",
        token_response=_TOKEN,
    )


# ---- happy paths -----------------------------------------------------------------------------


def test_404_when_the_skill_is_unknown(tmp_path: Path, _key: None) -> None:
    _ws(tmp_path / "ws")
    with _client(tmp_path / "ws", "alice") as client:
        res = client.post("/api/skills/does-not-exist/activate")
    assert res.status_code == 404
    assert "skill_not_found" in res.json()["detail"]


def test_200_when_the_skill_has_no_requirements(tmp_path: Path, _key: None) -> None:
    """A skill with no `requires_credentials` and no mcp_tool server returns 200 directly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "t", "name": "T"},
            }
        ),
        encoding="utf-8",
    )
    (ws / "skills").mkdir()
    (ws / "skills" / "no-deps.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {
                    "id": "no-deps",
                    "name": "No deps",
                    "description": "A deterministic LLM-only skill.",
                },
                "category": "capability",
                "implementation": {
                    "type": "llm_prompt",
                    "prompt": "say hi",
                },
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/no-deps/activate")
    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "skill_id": "no-deps",
        "requires_credentials": [],
    }


def test_200_when_every_credential_is_connected_explicit(tmp_path: Path, _key: None) -> None:
    ws = _ws(tmp_path / "ws", requires_credentials=["gmail"])
    _connect(ws, "alice")
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/gmail-search/activate")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["requires_credentials"] == ["gmail"]


def test_200_when_every_credential_is_connected_fallback(tmp_path: Path, _key: None) -> None:
    """The server-prefix fallback (mcp_tool skill without `requires_credentials`) still gates."""
    ws = _ws(tmp_path / "ws")  # no explicit requires_credentials
    _connect(ws, "alice")
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/gmail-search/activate")
    assert res.status_code == 200


# ---- 409 missing_credentials -----------------------------------------------------------------


def test_409_when_a_required_credential_is_missing(tmp_path: Path, _key: None) -> None:
    ws = _ws(tmp_path / "ws", requires_credentials=["gmail"])
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/gmail-search/activate")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "missing_credentials"
    assert detail["missing"] == [
        {
            "credential_id": "gmail",
            "issuer": "https://accounts.google.com",
            "setup_topology": "google-workspace-setup",
        }
    ]


def test_409_is_per_caller(tmp_path: Path, _key: None) -> None:
    """Bob's token does not help Alice. Preflight is scoped to the caller."""
    ws = _ws(tmp_path / "ws", requires_credentials=["gmail"])
    _connect(ws, "bob")
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/gmail-search/activate")
    assert res.status_code == 409
    with _client(ws, "bob") as client:
        res = client.post("/api/skills/gmail-search/activate")
    assert res.status_code == 200


def test_409_names_credential_even_without_a_setup_topology(tmp_path: Path, _key: None) -> None:
    """A provider the SETUP_TOPOLOGIES map does not know still returns a useful 409 —
    the credential_id is named; the setup_topology hint is just omitted."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "t", "name": "T"},
                "credentials": {
                    "linear": {
                        "source": "oauth",
                        "identity": "per-user",
                        "config": {"endpoint": "https://mcp.linear.app/mcp"},
                    }
                },
                "mcp_servers": [
                    {
                        "id": "linear",
                        "transport": "http",
                        "endpoint": "https://mcp.linear.app/mcp",
                        "credentials_ref": "linear",
                        "permission": "readonly",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ws / "skills").mkdir()
    (ws / "skills" / "linear-get.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {
                    "id": "linear-get",
                    "name": "Linear get",
                    "description": "Reads an issue via the linear server.",
                },
                "category": "capability",
                "implementation": {
                    "type": "mcp_tool",
                    "server": "linear",
                    "tool": "get_issue",
                },
                "requires_credentials": ["linear"],
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    with _client(ws, "alice") as client:
        res = client.post("/api/skills/linear-get/activate")
    assert res.status_code == 409
    missing = res.json()["detail"]["missing"]
    assert missing == [{"credential_id": "linear"}]
