"""Tests for the Mode B poll connector (verb execution, tier re-validation, poll cycle)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from swarmkit_runtime.connect import execute_command, poll_once


def _serve_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://serve")


@pytest.mark.asyncio
async def test_execute_capabilities_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/capabilities"
        return httpx.Response(200, json={"schema_version": "1.6.0", "topologies": ["hello"]})

    async with _serve_client(handler) as client:
        status, output, error = await execute_command(
            client,
            serve_url="http://127.0.0.1:8000",
            serve_token=None,
            granted_tier="read",
            verb="capabilities",
            args={},
        )
    assert status == "done"
    assert output is not None and output["schema_version"] == "1.6.0"
    assert error is None


@pytest.mark.asyncio
async def test_execute_run_substitutes_path_and_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = request.read().decode() or "{}"
        return httpx.Response(200, json={"job_id": "j1"})

    async with _serve_client(handler) as client:
        status, output, _ = await execute_command(
            client,
            serve_url="http://127.0.0.1:8000",
            serve_token="tok",
            granted_tier="run",
            verb="run",
            args={"topology_name": "hello", "body": {"input": "hi"}},
        )
    assert status == "done"
    assert seen["path"] == "/run/hello"
    assert seen["method"] == "POST"
    assert '"input"' in seen["body"]
    assert output == {"job_id": "j1"}


@pytest.mark.asyncio
async def test_execute_deploy_puts_artifact_to_api() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"updated": "hello"})

    async with _serve_client(handler) as client:
        status, output, _ = await execute_command(
            client,
            serve_url="http://127.0.0.1:8000",
            serve_token="tok",
            granted_tier="admin",
            verb="deploy",
            args={"kind": "topology", "id": "hello", "body": {"nodes": ["root"]}},
        )
    assert status == "done"
    assert seen["method"] == "PUT" and seen["path"] == "/api/topologies/hello"
    assert '"nodes"' in seen["body"]
    assert output == {"updated": "hello"}


@pytest.mark.asyncio
async def test_execute_deploy_needs_admin_and_known_kind() -> None:
    async with _serve_client(lambda r: httpx.Response(200, json={})) as client:
        # deploy requires admin
        s1, _, e1 = await execute_command(
            client,
            serve_url="http://x",
            serve_token=None,
            granted_tier="run",
            verb="deploy",
            args={"kind": "topology", "id": "hello", "body": {}},
        )
        # an undeployable kind is rejected
        s2, _, e2 = await execute_command(
            client,
            serve_url="http://x",
            serve_token=None,
            granted_tier="admin",
            verb="deploy",
            args={"kind": "workspace", "id": "w", "body": {}},
        )
    assert s1 == "error" and e1 is not None and "exceeds granted tier" in e1
    assert s2 == "error" and e2 is not None and "undeployable kind" in e2


@pytest.mark.asyncio
async def test_execute_rejects_verb_above_granted_tier() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    async with _serve_client(handler) as client:
        status, _, error = await execute_command(
            client,
            serve_url="http://127.0.0.1:8000",
            serve_token=None,
            granted_tier="read",
            verb="reload",  # needs admin
            args={},
        )
    assert status == "error"
    assert error is not None and "exceeds granted tier" in error
    assert called is False  # never reached serve


@pytest.mark.asyncio
async def test_execute_unknown_verb_and_missing_arg() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={})

    async with _serve_client(handler) as client:
        s1, _, e1 = await execute_command(
            client,
            serve_url="http://x",
            serve_token=None,
            granted_tier="admin",
            verb="explode",
            args={},
        )
        s2, _, e2 = await execute_command(
            client,
            serve_url="http://x",
            serve_token=None,
            granted_tier="read",
            verb="job-status",
            args={},  # missing job_id
        )
    assert s1 == "error" and e1 is not None and "unknown verb" in e1
    assert s2 == "error" and e2 is not None and "missing arg" in e2


@pytest.mark.asyncio
async def test_execute_serve_error_becomes_command_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _serve_client(handler) as client:
        status, _, error = await execute_command(
            client,
            serve_url="http://x",
            serve_token=None,
            granted_tier="read",
            verb="capabilities",
            args={},
        )
    assert status == "error"
    assert error is not None and "serve returned 500" in error


@pytest.mark.asyncio
async def test_poll_once_executes_and_reports_result() -> None:
    results: list[dict[str, Any]] = []

    def panel_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/poll"):
            return httpx.Response(
                200,
                json={"commands": [{"cmd_id": "c1", "verb": "capabilities", "args": {}}]},
            )
        if request.url.path.endswith("/result"):
            results.append({"path": request.url.path, "body": request.read().decode()})
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)  # pragma: no cover

    def serve_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"schema_version": "1.6.0"})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(panel_handler)) as panel,
        _serve_client(serve_handler) as serve,
    ):
        handled = await poll_once(
            panel,
            serve,
            panel_url="http://panel",
            instance_id="i1",
            granted_tier="read",
            serve_url="http://127.0.0.1:8000",
            serve_token=None,
            status_body={"status": "ok"},
        )
    assert handled == 1
    assert len(results) == 1
    assert results[0]["path"] == "/instances/i1/commands/c1/result"
    assert '"done"' in results[0]["body"]
