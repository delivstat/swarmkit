"""The first tool call of a model turn goes through governance like every later one.

Found by running tutorial level 5: a `permission: readonly` MCP server let `write_file` through
and a file appeared on disk. `check_mcp_permission` denied it when called directly. The
difference was the call site — `_dispatch_response` and the tool loop's synthesis branch passed
`governance` positionally into the `command_packs` slot (added in 1.197.0), so the FIRST tool call
of each turn executed with `governance=None` while retries were governed.

This drives a whole run with a scripted mock model that calls a readonly server's write tool on
its first turn, and asserts the call was refused — at the seam a unit test on
`check_mcp_permission` cannot see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: governed, name: Governed}
governance: {provider: mock}
mcp_servers:
  - id: fs
    transport: stdio
    command: ["python3", "-c", "pass"]
    permission: readonly
    effects: {read_file: read}
"""
_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata: {id: write-file, name: Write, description: Writes a file for the test.}
category: capability
implementation: {type: mcp_tool, server: fs, tool: write_file}
provenance: {authored_by: human, version: 1.0.0}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: files, version: 0.1.0}
agents:
  root:
    id: reader
    role: root
    model: {provider: mock, name: m}
    prompt: {system: write}
    skills: [write-file]
"""


class _Manager:
    """Enough of an MCP manager for the run: the tool is never reached if governance holds."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._cache_hits = 0
        self._cache_misses = 0

    def get_cached_result(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def cache_result(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return "readonly"

    def get_effects(self, server_id: str, tool_name: str) -> str:
        return "unknown"

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        return {"type": "object"}

    def get_server_cwd(self, server_id: str) -> str | None:
        return None

    async def start_required(self, required: set[str]) -> None:
        return None

    async def close_all(self) -> None:
        return None

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        # Never reached when governance holds; if it is, the assertion below names the bypass, so
        # what is returned here does not matter beyond being harmless.
        self.calls.append((server_id, tool_name, arguments))
        raise AssertionError("the readonly server's write tool was reached: governance bypassed")


@pytest.mark.asyncio
async def test_first_tool_call_is_governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "write-file.yaml").write_text(_SKILL)
    (ws / "topologies" / "files.yaml").write_text(_TOPO)

    rt = WorkspaceRuntime.from_workspace_path(ws)
    manager = _Manager()
    rt._mcp_manager = manager  # type: ignore[assignment]
    call = CompletionResponse(
        content=(
            ContentBlock(
                type="tool_use",
                tool_name="write-file",
                tool_input={"path": "x.txt", "content": "hi"},
                tool_use_id="t1",
            ),
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    provider = MockModelProvider(responses={("m", "Write hi"): call})
    registry = rt._provider_registry
    for key in list(getattr(registry, "_providers", {})):
        registry._providers[key] = provider

    result = await rt.run("files", "Write hi")

    # On the unfixed code this is [("fs", "write_file", {...})]: the tool ran ungoverned.
    assert manager.calls == [], "the readonly server's write tool was reached: governance bypassed"
    denials = [
        e
        for e in rt._governance.events  # type: ignore[attr-defined]
        if e.event_type == "skill.executed" and e.policy_decision == "deny"
    ]
    assert denials, f"no denial recorded; output was {result.output!r}"

    # And the DURABLE record says so. The hop from the provider's event to `audit_events` used to
    # drop `policy_decision`, so every persisted skill call read as neither allowed nor denied.
    import sqlite3  # noqa: PLC0415

    rows = (
        sqlite3.connect(ws / ".swarmkit" / "audit.sqlite")
        .execute(
            "select policy_decision, policy_reason from audit_events "
            "where event_type = 'skill.executed' and skill_id = 'write-file'"
        )
        .fetchall()
    )
    assert rows and all(r[0] == "deny" for r in rows), rows
    assert "readonly" in (rows[0][1] or "")
