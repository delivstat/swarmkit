"""Tests for ``POST /api/mcp/{server_id}/invoke`` — the direct-invoke route DOT and any equivalent
external client uses to call an MCP tool with per-user credential resolution done by the runtime.

The invariants under test come from the issue that spawned this route (#994):

- The endpoint never returns a resolved OAuth token.
- The endpoint refuses when the caller supplies an ``X-Owner`` for an identity they are not
  authorised to act as.
- An unknown ``server_id`` is a clean 404; the request never reaches the invoke path.
- The caller's identity — not a generic placeholder — is recorded as the ``agent_id`` on the
  audit path (checked by asserting on the argument to a spied ``governed_mcp_call``).
- The principal set for the duration of the call is the ``X-Owner`` (or the caller's identity
  when the header is absent), so per-user credential resolution sees the right owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime._principal import current_principal
from swarmkit_runtime.auth._provider import AuthIdentity
from swarmkit_runtime.mcp._governed import MCPCallDenied
from swarmkit_runtime.server import _routes_mcp_invoke


@dataclass
class _FakeToolResult:
    """Shaped like an MCP CallToolResult for the serializer to consume."""

    content: list[Any] = field(default_factory=list)
    isError: bool = False
    structuredContent: dict[str, Any] | None = None


@dataclass
class _FakeToolResponse:
    """Shaped like ``ToolResponse`` (has a ``data`` field carrying the CallToolResult)."""

    data: _FakeToolResult


@dataclass
class _FakeManager:
    """Minimal stand-in for MCPClientManager — the route only reads ``server_ids``."""

    server_ids: list[str]


@dataclass
class _FakeRuntime:
    """Minimal stand-in for WorkspaceRuntime — the route only reads two attributes."""

    mcp_manager: _FakeManager | None
    governance: Any = None


class _SpyInvoke:
    """Captures the arguments each invocation of ``governed_mcp_call`` is made with, so tests can
    assert on principal + agent_id + arguments without walking through the real MCP path."""

    def __init__(self, *, result: Any | None = None, raises: Exception | None = None) -> None:
        self.result = result or _FakeToolResponse(_FakeToolResult(content=[], isError=False))
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Capture the principal at the moment of the call — the whole point of `principal_scope`
        # is that it is set for the duration of this invocation, so any test wanting to check
        # that has to look here rather than after the fact.
        kwargs["_principal_at_call"] = current_principal()
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result


def _build_app(
    manager: _FakeManager | None,
    identity: AuthIdentity | None,
    *,
    spy: _SpyInvoke | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Wire an app carrying the fake runtime + identity + spied invoke, with the auth
    middleware bypassed (the route reads ``request.state.identity`` directly, so a middleware
    is not needed to exercise the handler itself)."""
    app = FastAPI()
    app.state.runtime = _FakeRuntime(mcp_manager=manager)

    @app.middleware("http")
    async def _put_identity_on_state(request: Any, call_next: Any) -> Any:
        request.state.identity = identity
        return await call_next(request)

    _routes_mcp_invoke._register_mcp_invoke_routes(app)

    if spy is not None:
        monkeypatch.setattr(_routes_mcp_invoke, "governed_mcp_call", spy)

    return TestClient(app)


def _identity(
    client_id: str = "srijith@delivstat.com", scopes: frozenset[str] = frozenset()
) -> AuthIdentity:
    return AuthIdentity(
        client_id=client_id,
        client_name="test",
        provider="test",
        scopes=scopes,
    )


# --- auth --------------------------------------------------------------------------------------


def test_unauthenticated_call_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no identity is on request.state, the route refuses. In production the auth middleware
    would answer first; a 401 here is the last-line defence that catches a middleware regression."""
    client = _build_app(_FakeManager(server_ids=["gmail"]), identity=None, monkeypatch=monkeypatch)
    res = client.post("/api/mcp/gmail/invoke", json={"tool": "search_threads"})
    assert res.status_code == 401


# --- owner scoping -----------------------------------------------------------------------------


def test_owner_matching_caller_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An X-Owner that names the caller's own identity resolves without an admin scope. The
    natural per-user case: the calling client is the credential's owner."""
    spy = _SpyInvoke()
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity("srijith@delivstat.com"),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    res = client.post(
        "/api/mcp/gmail/invoke",
        json={"tool": "search_threads"},
        headers={"X-Owner": "srijith@delivstat.com"},
    )
    assert res.status_code == 200
    assert spy.calls[-1]["_principal_at_call"] == "srijith@delivstat.com"


def test_owner_absent_falls_back_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing X-Owner picks up the caller's own identity — the natural default for a
    single-owner client."""
    spy = _SpyInvoke()
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity("owner@example.com"),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    res = client.post("/api/mcp/gmail/invoke", json={"tool": "search_threads"})
    assert res.status_code == 200
    assert spy.calls[-1]["_principal_at_call"] == "owner@example.com"


def test_owner_mismatch_without_admin_scope_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """An X-Owner that names a different identity than the caller, without the admin scope,
    is a widening move that must be refused with a clear reason."""
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity("client@example.com", scopes=frozenset()),
        monkeypatch=monkeypatch,
    )
    res = client.post(
        "/api/mcp/gmail/invoke",
        json={"tool": "search_threads"},
        headers={"X-Owner": "someone-else@example.com"},
    )
    assert res.status_code == 403
    assert "mcp:invoke:any-owner" in res.json()["detail"]


def test_owner_mismatch_with_admin_scope_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin scope exists precisely to let a service-shaped client (a scheduler, a fleet
    control-plane) invoke tools on behalf of a named user without impersonating them via the
    auth layer. Widening is deliberate, scope-gated, and audited via the calling identity."""
    spy = _SpyInvoke()
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity("service@example.com", scopes=frozenset({"mcp:invoke:any-owner"})),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    res = client.post(
        "/api/mcp/gmail/invoke",
        json={"tool": "search_threads"},
        headers={"X-Owner": "someone-else@example.com"},
    )
    assert res.status_code == 200
    assert spy.calls[-1]["_principal_at_call"] == "someone-else@example.com"
    # The audit path still records the CALLING identity, not the acted-on one — the widening
    # is visible in the trail (per the gateway's "shared servers must not attribute every call
    # to one agent" rule this route inherits).
    assert spy.calls[-1]["agent_id"] == "service@example.com"


# --- server lookup -----------------------------------------------------------------------------


def test_unknown_server_id_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity(),
        monkeypatch=monkeypatch,
    )
    res = client.post("/api/mcp/slack/invoke", json={"tool": "post_message"})
    assert res.status_code == 404
    assert "slack" in res.json()["detail"]


def test_no_mcp_manager_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace with no `mcp_servers` at all — the manager is None and there is nothing to
    invoke against. Clean 404 with a specific reason rather than a generic 500."""
    client = _build_app(manager=None, identity=_identity(), monkeypatch=monkeypatch)
    res = client.post("/api/mcp/gmail/invoke", json={"tool": "search_threads"})
    assert res.status_code == 404


# --- governance --------------------------------------------------------------------------------


def test_governance_denial_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``governed_mcp_call`` raises ``MCPCallDenied`` (policy refused, or a prerequisite
    was unmet), that surfaces as a 403 carrying the reason — never a 500."""
    spy = _SpyInvoke(raises=MCPCallDenied("permission tier is 'cautious' and no policy allows it"))
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity(),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    res = client.post("/api/mcp/gmail/invoke", json={"tool": "search_threads"})
    assert res.status_code == 403
    assert "cautious" in res.json()["detail"]


# --- happy path + credential invariant ---------------------------------------------------------


def test_happy_path_returns_tool_result_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of this endpoint. ``{"result": ...}`` carries the tool's own response;
    nothing about the resolved OAuth token, session id, transport detail, or credential owner
    that goes beyond what the tool itself emitted."""
    spy = _SpyInvoke(
        result=_FakeToolResponse(
            _FakeToolResult(
                content=[],
                isError=False,
                structuredContent={"threads": [{"id": "abc", "snippet": "hello"}]},
            )
        )
    )
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity("owner@example.com"),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    res = client.post(
        "/api/mcp/gmail/invoke",
        json={"tool": "search_threads", "arguments": {"query": "is:unread newer_than:1d"}},
    )
    assert res.status_code == 200
    body = res.json()
    # The tool's structuredContent is present, the isError flag is present, tokens are not.
    assert body == {
        "result": {
            "content": [],
            "isError": False,
            "structuredContent": {"threads": [{"id": "abc", "snippet": "hello"}]},
        }
    }
    # The most important assertion of this test file: nothing token-shaped anywhere in the body.
    serialised = res.text.lower()
    for banned in ("access_token", "refresh_token", "authorization", "bearer "):
        assert banned not in serialised, f"response leaked {banned!r}"


def test_arguments_are_forwarded_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool's arguments are the caller's contract with the tool — the route must not
    transform or filter them, or a caller cannot rely on tool semantics matching what the
    server documents."""
    spy = _SpyInvoke()
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity(),
        spy=spy,
        monkeypatch=monkeypatch,
    )
    args = {"query": "is:unread", "limit": 20, "nested": {"a": 1}}
    res = client.post("/api/mcp/gmail/invoke", json={"tool": "search_threads", "arguments": args})
    assert res.status_code == 200
    assert spy.calls[-1]["arguments"] == args


def test_empty_tool_name_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pydantic validation catches an empty tool name before the route touches the runtime."""
    client = _build_app(
        _FakeManager(server_ids=["gmail"]),
        identity=_identity(),
        monkeypatch=monkeypatch,
    )
    res = client.post("/api/mcp/gmail/invoke", json={"tool": ""})
    assert res.status_code == 422  # FastAPI's pydantic-validation error is 422, not 400
