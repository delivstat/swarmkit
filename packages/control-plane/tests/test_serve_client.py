"""Unit tests for ServeClient + resolve_secret_ref (httpx MockTransport, no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from swarmkit_control_plane._serve_client import (
    ConnectorError,
    ServeClient,
    resolve_secret_ref,
)


def _client(handler: Any, **kw: Any) -> ServeClient:
    return ServeClient("http://serve:8000/", "tok", transport=httpx.MockTransport(handler), **kw)


# ---- resolve_secret_ref -----------------------------------------------------


def test_resolve_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CP_TOK", "abc")
    assert resolve_secret_ref("env:CP_TOK") == "abc"
    assert resolve_secret_ref("env:MISSING") is None


def test_resolve_file(tmp_path: Path) -> None:
    f = tmp_path / "tok.txt"
    f.write_text("  s3cret\n", encoding="utf-8")
    assert resolve_secret_ref(f"file:{f}") == "s3cret"
    assert resolve_secret_ref("file:/no/such/path") is None


def test_resolve_literal_and_empty() -> None:
    assert resolve_secret_ref("literal") == "literal"
    assert resolve_secret_ref("") is None


# ---- ServeClient ------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_header_and_base_join() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as serve:
        resp = await serve.get("/capabilities")
    assert seen["url"] == "http://serve:8000/capabilities"  # base rstrip + path join
    assert seen["auth"] == "Bearer tok"
    assert serve.ok(resp, "/capabilities") == {"ok": True}


@pytest.mark.asyncio
async def test_get_auth_false_omits_header() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    async with _client(handler) as serve:
        await serve.get("/health", auth=False)
    assert seen["auth"] is None


@pytest.mark.asyncio
async def test_ok_maps_auth_and_error_statuses() -> None:
    async with _client(lambda r: httpx.Response(403, text="nope")) as serve:
        with pytest.raises(ConnectorError, match="auth failed"):
            serve.ok(await serve.get("/jobs"), "/jobs")
    async with _client(lambda r: httpx.Response(500, text="boom")) as serve:
        with pytest.raises(ConnectorError, match="returned 500"):
            serve.ok(await serve.get("/jobs"), "/jobs")


@pytest.mark.asyncio
async def test_transport_error_becomes_connector_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _client(boom) as serve:
        with pytest.raises(ConnectorError, match="cannot reach http://serve:8000"):
            await serve.get("/capabilities")


@pytest.mark.asyncio
async def test_post_and_put_send_json_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.method] = request.read().decode()
        return httpx.Response(200, json={"done": True})

    async with _client(handler) as serve:
        await serve.post("/run/hello", {"input": "hi"})
        await serve.put("/api/topologies/hello", {"nodes": ["root"]})
    assert '"input"' in seen["POST"] and '"nodes"' in seen["PUT"]


@pytest.mark.asyncio
async def test_delete_sends_method_and_auth() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ejected": "m1"})

    async with _client(handler) as serve:
        resp = await serve.delete("/fleet/membership/m1")
    assert seen["method"] == "DELETE" and seen["path"] == "/fleet/membership/m1"
    assert seen["auth"] == "Bearer tok"
    assert serve.ok(resp, "/fleet/membership") == {"ejected": "m1"}


@pytest.mark.asyncio
async def test_delete_transport_error_is_connector_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ConnectorError):
        async with _client(boom) as serve:
            await serve.delete("/fleet/membership/m1")


@pytest.mark.asyncio
async def test_ok_non_json_body() -> None:
    async with _client(lambda r: httpx.Response(200, text="plain")) as serve:
        assert serve.ok(await serve.get("/x"), "/x") == {"text": "plain"}


def test_no_token_means_no_header() -> None:
    sc = ServeClient("http://serve", "")
    assert sc._headers == {}
