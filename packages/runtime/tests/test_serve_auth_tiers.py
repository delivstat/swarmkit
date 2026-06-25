"""Tests for serve transport-auth tiers, per-route enforcement, and the reserved-scope guard.

Phase 3 auth (slice 1). See design/details/control-plane/12-auth.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from swarmkit_runtime.auth import (
    SERVE_ADMIN,
    SERVE_READ,
    SERVE_RUN,
    APIKeyAuthProvider,
    AuthIdentity,
    NoneAuthProvider,
    expand_tier,
    reserved_violations,
)
from swarmkit_runtime.auth._secrets import resolve_secret_ref
from swarmkit_runtime.cli import _auth_requires_secure, app
from swarmkit_runtime.server import _required_action
from typer.testing import CliRunner

# --- tier expansion ---------------------------------------------------------


def test_tier_expansion_is_cumulative() -> None:
    assert expand_tier("read") == {SERVE_READ}
    assert expand_tier("run") == {SERVE_READ, SERVE_RUN}
    assert expand_tier("admin") == {SERVE_READ, SERVE_RUN, SERVE_ADMIN}
    assert expand_tier("RUN") == {SERVE_READ, SERVE_RUN}  # case-insensitive
    assert expand_tier("bogus") == frozenset()


def test_api_key_tier_expands_to_scopes() -> None:
    p = APIKeyAuthProvider(keys=[{"key_ref": "literal-secret", "client_id": "cp", "tier": "run"}])
    # one key registered with run-tier scopes
    entry = next(iter(p._keys.values()))
    assert entry.scopes == {SERVE_READ, SERVE_RUN}


def test_api_key_explicit_scopes_still_work() -> None:
    p = APIKeyAuthProvider(keys=[{"key_ref": "s", "client_id": "cp", "scopes": ["serve:read"]}])
    assert next(iter(p._keys.values())).scopes == {"serve:read"}


# --- reserved-scope guard ---------------------------------------------------


def test_reserved_violations_detects_governance_scopes() -> None:
    assert reserved_violations(frozenset({"serve:read"})) == frozenset()
    assert "topologies:modify" in reserved_violations(frozenset({"topologies:modify"}))
    assert "audit:read" in reserved_violations(frozenset({"audit:read"}))  # audit:* family


def test_api_key_rejects_reserved_scope() -> None:
    with pytest.raises(ValueError, match="reserved governance scope"):
        APIKeyAuthProvider(
            keys=[{"key_ref": "s", "client_id": "evil", "scopes": ["skills:activate"]}]
        )


# --- route → required action map --------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/health", None),
        ("GET", "/topologies", "read"),
        ("GET", "/jobs/abc", "read"),
        ("GET", "/api/topologies/x/yaml", "read"),
        ("POST", "/run/hello", "run"),
        ("POST", "/hooks/hello", "run"),
        ("POST", "/conversations", "run"),
        ("POST", "/mcp", "run"),
        ("PUT", "/api/topologies/x", "admin"),
        ("POST", "/api/topologies", "admin"),
        ("DELETE", "/api/topologies/x", "admin"),
        ("POST", "/api/reload", "admin"),
        ("POST", "/canary/hello/promote", "admin"),
        ("POST", "/canary/hello/rollback", "admin"),
    ],
)
def test_required_action(method: str, path: str, expected: str | None) -> None:
    assert _required_action(method, path) == expected


# --- authorize enforcement (provider semantics) -----------------------------


def _identity(scopes: Iterable[str]) -> AuthIdentity:
    return AuthIdentity(
        client_id="c", client_name="C", provider="api_key", scopes=frozenset(scopes)
    )


@pytest.mark.asyncio
async def test_run_token_denied_admin_route() -> None:
    p = APIKeyAuthProvider(keys=[{"key_ref": "s", "client_id": "cp", "tier": "run"}])
    ident = _identity(expand_tier("run"))
    assert await p.authorize(ident, "serve", "read") is True
    assert await p.authorize(ident, "serve", "run") is True
    assert await p.authorize(ident, "serve", "admin") is False  # run < admin


@pytest.mark.asyncio
async def test_read_token_denied_run() -> None:
    p = APIKeyAuthProvider(keys=[{"key_ref": "s", "client_id": "cp", "tier": "read"}])
    ident = _identity(expand_tier("read"))
    assert await p.authorize(ident, "serve", "read") is True
    assert await p.authorize(ident, "serve", "run") is False


@pytest.mark.asyncio
async def test_none_provider_authorizes_everything() -> None:
    p = NoneAuthProvider()
    assert await p.authorize(_identity({"*"}), "serve", "admin") is True


# --- default-secure helper --------------------------------------------------


def test_auth_requires_secure_defaults_true(tmp_path: Path) -> None:
    # no workspace.yaml → default-secure
    assert _auth_requires_secure(tmp_path) is True
    # block present but flag unset → default True
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: x, name: X}\n"
        "server:\n  auth:\n    provider: none\n"
    )
    assert _auth_requires_secure(tmp_path) is True


def test_auth_requires_secure_opt_out(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: x, name: X}\n"
        "server:\n  auth:\n    provider: none\n    require_on_nonloopback: false\n"
    )
    assert _auth_requires_secure(tmp_path) is False


# --- key_ref secret resolution ----------------------------------------------


def test_resolve_env_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOK", "s3cr3t")
    assert resolve_secret_ref("env:MY_TOK") == "s3cr3t"
    monkeypatch.delenv("MY_TOK", raising=False)
    assert resolve_secret_ref("env:MY_TOK") is None


def test_resolve_file_ref(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("file-secret\n")  # trailing newline stripped
    assert resolve_secret_ref(f"file:{f}") == "file-secret"
    assert resolve_secret_ref(f"file:{tmp_path / 'missing'}") is None


def test_resolve_credentials_env_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRED_VAR", "from-cred")
    creds = {"panel": {"source": "env", "config": {"env": "CRED_VAR"}}}
    assert resolve_secret_ref("credentials:panel", creds) == "from-cred"


def test_resolve_credentials_file_source(tmp_path: Path) -> None:
    f = tmp_path / "c"
    f.write_text("cred-file")
    creds = {"panel": {"source": "file", "config": {"path": str(f)}}}
    assert resolve_secret_ref("credentials:panel", creds) == "cred-file"


def test_resolve_credentials_cloud_source_raises() -> None:
    creds = {"v": {"source": "hashicorp-vault", "config": {}}}
    with pytest.raises(NotImplementedError, match="SecretsProvider"):
        resolve_secret_ref("credentials:v", creds)


def test_resolve_literal_passthrough() -> None:
    assert resolve_secret_ref("just-a-literal") == "just-a-literal"


def test_api_key_resolves_credentials_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRED_VAR", "the-secret")
    p = APIKeyAuthProvider(
        keys=[{"key_ref": "credentials:panel", "client_id": "cp", "tier": "read"}],
        credentials={"panel": {"source": "env", "config": {"env": "CRED_VAR"}}},
    )
    assert "the-secret" in p._keys


# --- token mint command -----------------------------------------------------


def test_auth_token_mint() -> None:
    result = CliRunner().invoke(app, ["auth", "token", "control-plane", "--tier", "run"])
    assert result.exit_code == 0
    assert "key_ref: env:CONTROL_PLANE_TOKEN" in result.stdout
    assert "client_id: control-plane" in result.stdout
    assert "tier: run" in result.stdout
    assert "Bearer " in result.stdout


def test_auth_token_invalid_tier() -> None:
    result = CliRunner().invoke(app, ["auth", "token", "cp", "--tier", "superuser"])
    assert result.exit_code == 2
