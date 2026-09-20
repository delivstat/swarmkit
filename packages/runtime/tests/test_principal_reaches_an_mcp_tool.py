"""A topology invoked as an MCP tool still knows who called it.

`swarmkit serve` publishes every topology as a `run_<name>` tool on `/mcp/`, so an assistant in
Claude Desktop or Cursor can call a swarm directly. That path does not go through `POST /run`: the
tool calls `WorkspaceRuntime.run` itself, and the streamable-HTTP transport runs a session manager
whose task group is started in the app's *lifespan*, not per request.

Which raised a real question for `identity: per-user` credentials: a task created from the lifespan
context would not inherit the principal the auth middleware sets per request, and every per-user
connection reached through an MCP tool would refuse with "no authenticated caller" — a whole entry
point quietly unable to act as anyone.

It works, and this test is why it keeps working. The property depends on the MCP SDK continuing to
dispatch a tool call inside the request's context; nothing in this repo guarantees that, and an SDK
upgrade could change it without any signal other than per-user credentials mysteriously refusing.
Asserting it here turns that into a failing test instead of a support question.

See design/details/per-caller-credential-delegation.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import mcp.client.streamable_http as streamable
import pytest
import yaml
from mcp import ClientSession
from swarmkit_runtime._principal import current_principal
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.auth import NoneAuthProvider
from swarmkit_runtime.server._app import create_app


def _workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "mcp-caller", "name": "MCP Caller"},
            }
        ),
        encoding="utf-8",
    )
    topologies = tmp_path / "topologies"
    topologies.mkdir(exist_ok=True)
    (topologies / "hello.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "hello", "version": "0.1.0"},
                "agents": {"root": {"id": "a", "role": "root", "prompt": {"system": "hi"}}},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_a_topology_called_as_an_mcp_tool_sees_its_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def _probe(self: Any, *args: Any, **kwargs: Any) -> Any:
        # What a credential resolution during this tool call would see. Stubbed so the test needs
        # no model provider — the question is the context, not the answer.
        seen["principal"] = current_principal()

        class _Result:
            output = "ok"

        return _Result()

    monkeypatch.setattr(WorkspaceRuntime, "run", _probe)

    app = create_app(_workspace(tmp_path / "ws"), auth_provider=NoneAuthProvider(identity="alice"))
    transport = httpx.ASGITransport(app=app)

    def _client(
        headers: dict[str, str] | None = None,
        timeout: Any = None,
        auth: Any = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers or {},
            timeout=30,
        )

    async with (
        app.router.lifespan_context(app),
        streamable.streamablehttp_client("http://test/mcp/", httpx_client_factory=_client) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        names = [t.name for t in (await session.list_tools()).tools]
        assert "run_hello" in names, f"topology not published as a tool: {names}"
        await asyncio.wait_for(session.call_tool("run_hello", {"input": "anything"}), timeout=60)

    assert seen.get("principal") == "alice", (
        "a topology called through /mcp/ must see the authenticated caller, or every "
        "`identity: per-user` credential reached this way refuses with 'no authenticated caller'"
    )
