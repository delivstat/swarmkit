"""One credential service, every entry point.

`design/details/credential-service.md`. The bug this replaces was not a missing refresh — it was a
refresh written at *one call site*, so `chat`, `serve` and `mcp-serve` silently skipped it. The
tests that matter here are therefore about the *seam*: that resolution refreshes, that the MCP
client resolves at the point of use rather than at connect time, and that no entry point can
acquire a manager without the service.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.credentials import ConsentRequired, CredentialError, CredentialService
from swarmkit_runtime.mcp._client import MCPClientManager, MCPServerConfig
from swarmkit_runtime.oauth import KEY_ENV, TokenStore

TOKEN_ENDPOINT = "https://auth.example/token"
issued: list[str] = []


def _provider(status: int = 200) -> httpx.MockTransport:
    def handler(_: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, text="invalid_grant")
        issued.append(f"fresh-{len(issued)}")
        return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 3600})

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Route the service's own AsyncClient at a stub, keeping its other kwargs."""
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("swarmkit_runtime.credentials._service.httpx.AsyncClient", factory)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    issued.clear()
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv("SWARMKIT_OAUTH_RUN_WINDOW_S", raising=False)


def _store_with(tmp_path: Path, *, expires_in: float) -> TokenStore:
    store = TokenStore(tmp_path)
    store.save(
        credential_id="linear",
        owner="srijith",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response={
            "access_token": "stale-access",
            "refresh_token": "refresh-1",
            "expires_in": expires_in,
        },
        metadata={"token_endpoint": TOKEN_ENDPOINT, "client_id": "c1"},
    )
    return store


OAUTH_BLOCK = {"linear": {"source": "oauth", "config": {"owner": "srijith"}}}


# ---- the ordinary sources still work ----------------------------------------------------------


@pytest.mark.asyncio
async def test_env_and_file_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_file = tmp_path / "token.txt"
    secret_file.write_text("  from-a-file\n")
    monkeypatch.setenv("A_TOKEN", "from-the-env")
    service = CredentialService(
        tmp_path,
        {
            "e": {"source": "env", "config": {"env": "A_TOKEN"}},
            "f": {"source": "file", "config": {"path": str(secret_file)}},
        },
    )
    assert await service.resolve("e") == "from-the-env"
    assert await service.resolve("f") == "from-a-file"


@pytest.mark.asyncio
async def test_an_unset_env_var_names_itself(tmp_path: Path) -> None:
    service = CredentialService(tmp_path, {"e": {"source": "env", "config": {"env": "NOPE"}}})
    with pytest.raises(CredentialError, match="resolved to nothing"):
        await service.resolve("e")


@pytest.mark.asyncio
async def test_an_undeclared_credential_lists_what_is_declared(tmp_path: Path) -> None:
    service = CredentialService(tmp_path, {"a": {"source": "env", "config": {"env": "X"}}})
    with pytest.raises(CredentialError, match=r"Declared: \['a'\]"):
        await service.resolve("missing")


@pytest.mark.asyncio
async def test_an_unwired_cloud_source_says_so(tmp_path: Path) -> None:
    service = CredentialService(tmp_path, {"v": {"source": "hashicorp-vault", "config": {}}})
    with pytest.raises(CredentialError, match="not wired yet"):
        await service.resolve("v")


# ---- the point of the whole change -------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolving_an_expiring_oauth_credential_refreshes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh is a property of resolution, not a step somebody remembers to call."""
    store = _store_with(tmp_path, expires_in=30)
    service = CredentialService(tmp_path, OAUTH_BLOCK)
    service._store = store

    _patch_client(monkeypatch, _provider())
    assert await service.resolve("linear") == "fresh-0"
    assert store.access_token("linear", "srijith") == "fresh-0"


@pytest.mark.asyncio
async def test_a_healthy_token_is_returned_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_with(tmp_path, expires_in=86_400)
    service = CredentialService(tmp_path, OAUTH_BLOCK)
    service._store = store
    _patch_client(monkeypatch, _provider())
    assert await service.resolve("linear") == "stale-access"
    assert issued == []


@pytest.mark.asyncio
async def test_a_refusal_surfaces_as_consent_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_with(tmp_path, expires_in=30)
    service = CredentialService(tmp_path, OAUTH_BLOCK)
    service._store = store
    _patch_client(monkeypatch, _provider(400))
    with pytest.raises(ConsentRequired):
        await service.resolve("linear")


@pytest.mark.asyncio
async def test_an_oauth_credential_with_no_stored_token_says_how_to_get_one(
    tmp_path: Path,
) -> None:
    service = CredentialService(tmp_path, OAUTH_BLOCK)
    with pytest.raises(CredentialError, match="Connect it on the Connections page"):
        await service.resolve("linear")


def test_sync_resolution_refuses_oauth_rather_than_returning_a_stale_token(
    tmp_path: Path,
) -> None:
    """Being unable to refresh is a different answer from having refreshed. Quietly returning the
    stored bytes would reintroduce the bug this service exists to remove."""
    service = CredentialService(tmp_path, OAUTH_BLOCK)
    with pytest.raises(CredentialError, match="must be resolved asynchronously"):
        service.resolve_sync("linear")


# ---- the MCP client resolves at the point of use ------------------------------------------------


class _FakeService:
    """Hands out whatever token the test currently wants."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = 0

    async def resolve(self, _ref: str) -> str:
        self.calls += 1
        return self.token


@pytest.mark.asyncio
async def test_an_http_session_is_reopened_when_the_token_changes(tmp_path: Path) -> None:
    """The connect-time-binding bug, as a test.

    A header resolved once and held for the session's life pins whatever was valid at startup —
    under `serve` that can be days stale, and the refresh would update the store while changing
    nothing on the wire.
    """
    service = _FakeService("token-1")
    manager = MCPClientManager(
        {
            "linear": MCPServerConfig(
                server_id="linear",
                transport="http",
                endpoint="https://x/mcp",
                credentials_ref="linear",
            )
        },
        workspace_root=tmp_path,
        credential_service=service,
    )
    sentinel = object()
    manager._sessions["linear"] = sentinel  # type: ignore[assignment]
    manager._session_credentials["linear"] = "token-1"

    # Unchanged: the open session is reused.
    assert await manager.get_session("linear") is sentinel

    # Changed: the session is dropped so the next call reconnects with the fresh header.
    service.token = "token-2"
    assert await manager._credential_changed("linear") is True
    await manager._drop_session("linear")
    assert "linear" not in manager._sessions


@pytest.mark.asyncio
async def test_a_stdio_server_is_not_reconnected(tmp_path: Path) -> None:
    """Its secret goes into the child's environment at spawn: connect time IS the point of use."""
    service = _FakeService("token-1")
    manager = MCPClientManager(
        {
            "git": MCPServerConfig(
                server_id="git", transport="stdio", command=["true"], credentials_ref="git"
            )
        },
        workspace_root=tmp_path,
        credential_service=service,
    )
    service.token = "token-2"
    assert await manager._credential_changed("git") is False


@pytest.mark.asyncio
async def test_a_server_with_no_credential_never_calls_the_service(tmp_path: Path) -> None:
    service = _FakeService("t")
    manager = MCPClientManager(
        {"git": MCPServerConfig(server_id="git", transport="stdio", command=["true"])},
        workspace_root=tmp_path,
        credential_service=service,
    )
    assert await manager._resolved_credential("git") is None
    assert service.calls == 0


# ---- no entry point can get a manager without the service ---------------------------------------


def test_the_runtime_builds_its_manager_with_a_credential_service(tmp_path: Path) -> None:
    """The assertion whose absence let `serve` skip refreshing entirely.

    Every entry point — run, chat, serve, mcp-serve — reaches MCP through a manager built here. If
    it is built without the service, none of them refresh, and no test of the service itself would
    notice.
    """
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "metadata: {id: t, name: T}\n"
        "governance: {provider: mock, policy_language: yaml}\n"
        "credentials:\n"
        "  tok: {source: env, config: {env: SOME_TOKEN}}\n"
        "mcp_servers:\n"
        "  - id: remote\n"
        "    transport: http\n"
        "    endpoint: https://x/mcp\n"
        "    credentials_ref: tok\n"
    )
    runtime = WorkspaceRuntime.from_workspace_path(tmp_path)
    manager = runtime._mcp_manager
    assert manager is not None
    assert isinstance(manager._credential_service, CredentialService)


def test_expiry_skew_is_respected_by_the_service(tmp_path: Path) -> None:
    """A token with seconds left must count as expiring, or it dies mid-call."""
    store = _store_with(tmp_path, expires_in=30)
    meta = store.metadata("linear", "srijith")
    assert meta is not None
    assert meta.expired is True
    assert meta.expires_at is not None
    assert meta.expires_at > time.time()
