"""Tests for MCP provenance envelope (ToolMetadata + ToolResponse).

Verifies that every MCP tool call returns a ToolResponse with provenance
metadata, and that the skill executor appends the provenance tag to output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from swarmkit_runtime.langgraph_compiler._skill_executor import _execute_mcp_tool
from swarmkit_runtime.mcp._client import (
    MCPClientManager,
    MCPServerConfig,
    ToolMetadata,
    ToolResponse,
)
from swarmkit_runtime.skills import ResolvedSkill

# ---- ToolMetadata / ToolResponse dataclasses -----------------------------


class TestToolMetadata:
    def test_source_format(self) -> None:
        meta = ToolMetadata(
            source="cdt-server:search-config",
            server_id="cdt-server",
            duration_ms=42,
        )
        assert meta.source == "cdt-server:search-config"
        assert meta.server_id == "cdt-server"
        assert meta.duration_ms == 42

    def test_frozen(self) -> None:
        meta = ToolMetadata(source="s:t")
        with pytest.raises(AttributeError):
            meta.source = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        meta = ToolMetadata(source="s:t")
        assert meta.args is None
        assert meta.timestamp == ""
        assert meta.duration_ms == 0
        assert meta.server_id == ""


class TestToolResponse:
    def test_wraps_call_tool_result(self) -> None:
        mock_result = MagicMock()
        mock_result.content = []
        meta = ToolMetadata(source="srv:tool", duration_ms=10, server_id="srv")
        resp = ToolResponse(data=mock_result, metadata=meta)
        assert resp.data is mock_result
        assert resp.metadata.source == "srv:tool"

    def test_frozen(self) -> None:
        mock_result = MagicMock()
        meta = ToolMetadata(source="s:t")
        resp = ToolResponse(data=mock_result, metadata=meta)
        with pytest.raises(AttributeError):
            resp.metadata = meta  # type: ignore[misc]


# ---- MCPClientManager.call_tool ------------------------------------------


class TestCallToolProvenance:
    @pytest.mark.asyncio
    async def test_call_tool_returns_tool_response(self) -> None:
        mock_session = AsyncMock()
        mock_call_result = MagicMock()
        mock_call_result.content = [MagicMock(text="hello", type="text")]
        mock_session.call_tool.return_value = mock_call_result

        manager = MCPClientManager(
            servers={"test-server": MCPServerConfig(server_id="test-server")}
        )
        manager._sessions["test-server"] = mock_session

        response = await manager.call_tool("test-server", "my-tool", {"key": "val"})

        assert isinstance(response, ToolResponse)
        assert response.data is mock_call_result
        assert response.metadata.source == "test-server:my-tool"
        assert response.metadata.server_id == "test-server"
        assert response.metadata.args == {"key": "val"}
        assert response.metadata.duration_ms >= 0
        assert response.metadata.timestamp != ""

    @pytest.mark.asyncio
    async def test_call_tool_records_timing(self) -> None:
        mock_session = AsyncMock()
        mock_call_result = MagicMock()
        mock_call_result.content = []
        mock_session.call_tool.return_value = mock_call_result

        manager = MCPClientManager(servers={"srv": MCPServerConfig(server_id="srv")})
        manager._sessions["srv"] = mock_session

        response = await manager.call_tool("srv", "slow-tool")
        assert response.metadata.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_call_tool_none_arguments(self) -> None:
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(content=[])

        manager = MCPClientManager(servers={"srv": MCPServerConfig(server_id="srv")})
        manager._sessions["srv"] = mock_session

        response = await manager.call_tool("srv", "tool-no-args")
        assert response.metadata.args is None


# ---- Skill executor provenance tag ---------------------------------------


class TestSkillExecutorProvenance:
    @pytest.mark.asyncio
    async def test_provenance_appended_to_output(self) -> None:
        mock_skill = MagicMock(spec=ResolvedSkill)
        mock_skill.id = "test-skill"
        mock_skill.raw = MagicMock()
        mock_skill.raw.implementation = {
            "type": "mcp_tool",
            "server": "test-srv",
            "tool": "search",
        }

        mock_call_result = MagicMock()
        mock_text_block = MagicMock()
        mock_text_block.text = "Found 3 results"
        mock_text_block.type = "text"
        mock_call_result.content = [mock_text_block]

        meta = ToolMetadata(
            source="test-srv:search",
            args={"query": "test"},
            timestamp="2026-05-18T12:00:00Z",
            duration_ms=150,
            server_id="test-srv",
        )
        tool_response = ToolResponse(data=mock_call_result, metadata=meta)

        mock_manager = MagicMock()
        mock_manager.call_tool = AsyncMock(return_value=tool_response)
        mock_manager.get_permission.return_value = "open"
        mock_manager.get_server_cwd.return_value = None
        mock_manager.get_cached_result.return_value = None
        mock_manager._cache_misses = 0
        mock_manager.cache_result = MagicMock()

        result = await _execute_mcp_tool(
            mock_skill,
            input_text='{"query": "test"}',
            mcp_manager=mock_manager,
        )

        assert isinstance(result, str)
        assert "Found 3 results" in result
        assert "[source: test-srv:search | 150ms]" in result
