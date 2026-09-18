"""`/mcp/` on serve answers an MCP client, and each topology is a tool there.

The streamable-HTTP app was mounted but its session manager was never run, so every request was a
500 ("Task group is not initialized") — and the transport's own path inside the mount made the
real URL `/mcp/mcp`. The endpoint had a design note, a config flag, a startup log line and no
working request. Exercised here with the SDK's own client against the app over ASGI.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.mark.asyncio
async def test_a_topology_runs_as_an_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    app = create_app(EXAMPLE_WS)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)

        def _client(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
                headers=headers,
                timeout=timeout,
                auth=auth,
                follow_redirects=True,
            )

        # The middleware registers the tools on the first request through the app.
        async with _client() as probe:
            await probe.get("/health")
        async with (
            streamablehttp_client("http://127.0.0.1/mcp/", httpx_client_factory=_client) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert "run_hello" in names, names
            result = await session.call_tool("run_hello", {"input": "hi"})
            assert result.content and "mock response" in result.content[0].text  # type: ignore[union-attr]
