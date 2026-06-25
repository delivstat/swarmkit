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
from swarmkit_runtime.cli import _auth_requires_secure
from swarmkit_runtime.server import _required_action

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
