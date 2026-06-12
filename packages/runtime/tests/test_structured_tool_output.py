"""Tests for surfacing MCP structured tool output.

When a tool returns structured output (MCP ``structuredContent`` / output
schema) but no text fallback block — permitted by the MCP spec — the skill
executor must surface the structured data instead of reading as empty. When a
text block is present (the common FastMCP case, which serialises the payload
into text), text remains primary so existing tools are unaffected.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from swarmkit_runtime.langgraph_compiler._skill_executor import _execute_mcp_tool
from swarmkit_runtime.mcp._client import ToolMetadata, ToolResponse
from swarmkit_runtime.skills import ResolvedSkill


def _skill() -> MagicMock:
    skill = MagicMock(spec=ResolvedSkill)
    skill.id = "test-skill"
    skill.raw = MagicMock()
    skill.raw.implementation = {"type": "mcp_tool", "server": "srv", "tool": "detect"}
    return skill


def _manager(tool_response: ToolResponse) -> MagicMock:
    manager = MagicMock()
    manager.call_tool = AsyncMock(return_value=tool_response)
    manager.get_permission.return_value = "open"
    manager.get_server_cwd.return_value = None
    manager.get_cached_result.return_value = None
    manager._cache_misses = 0
    manager.cache_result = MagicMock()
    return manager


@pytest.mark.asyncio
async def test_structured_content_surfaced_when_no_text() -> None:
    """A tool returning only structuredContent (no text block) is surfaced."""
    result = MagicMock()
    non_text = MagicMock()
    non_text.text = None
    non_text.type = "resource"
    result.content = [non_text]
    result.structuredContent = {"found": True, "count": 2, "camera": "Main-Door"}

    resp = ToolResponse(data=result, metadata=ToolMetadata(source="srv:detect", server_id="srv"))
    out = await _execute_mcp_tool(_skill(), input_text="{}", mcp_manager=_manager(resp))

    assert isinstance(out, str)
    assert '"found": true' in out
    assert '"camera": "Main-Door"' in out
    assert json.loads(out.split("\n[source:")[0])["count"] == 2


@pytest.mark.asyncio
async def test_text_remains_primary_over_structured() -> None:
    """When a text block is present it wins — existing tools are unaffected."""
    result = MagicMock()
    text_block = MagicMock()
    text_block.text = "human readable result"
    text_block.type = "text"
    result.content = [text_block]
    result.structuredContent = {"result": "human readable result"}  # FastMCP wrapper

    resp = ToolResponse(data=result, metadata=ToolMetadata(source="srv:detect", server_id="srv"))
    out = await _execute_mcp_tool(_skill(), input_text="{}", mcp_manager=_manager(resp))

    assert "human readable result" in out
    # The {"result": ...} wrapper must NOT leak into the output.
    assert '{"result"' not in out


@pytest.mark.asyncio
async def test_empty_result_reports_no_response() -> None:
    """No text and no structured content still yields the clear sentinel."""
    result = MagicMock()
    result.content = []
    result.structuredContent = None

    resp = ToolResponse(data=result, metadata=ToolMetadata(source="srv:detect", server_id="srv"))
    out = await _execute_mcp_tool(_skill(), input_text="{}", mcp_manager=_manager(resp))

    assert "(no response from MCP)" in out
