"""Tests for GBrain-backed workspace memory.

Covers:
- Page building (frontmatter + markdown)
- save_memory via MCP put_page
- search_memory via MCP query
- extract_facts delegation
- recall delegation
- delete_memory and list_memories
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from swarmkit_runtime.memory._gbrain import GBrainMemory, _build_memory_page

# ---------------------------------------------------------------------------
# Page builder
# ---------------------------------------------------------------------------


def test_build_memory_page_full() -> None:
    page = _build_memory_page(
        slug="memory-alice-20260528T120000",
        topic="Grief and letting go",
        context="User dealing with loss of a friend",
        key_points=["Katha 2.19 resonated", "Impermanence as comfort"],
        tags=["grief", "katha"],
        user="alice",
        session_id="conv-12",
        agent_id="advisor",
    )
    assert "type: memory" in page
    assert "user: alice" in page
    assert "session: conv-12" in page
    assert "# Grief and letting go" in page
    assert "- Katha 2.19 resonated" in page
    assert "tags: [grief, katha]" in page


def test_build_memory_page_minimal() -> None:
    page = _build_memory_page(
        slug="memory-20260528T120000",
        topic="Quick note",
        context="",
        key_points=[],
        tags=[],
        user=None,
        session_id=None,
        agent_id=None,
    )
    assert "type: memory" in page
    assert "# Quick note" in page
    assert "user:" not in page


# ---------------------------------------------------------------------------
# GBrainMemory with mocked MCP
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_mcp() -> AsyncMock:
    mcp = AsyncMock()

    async def call_tool(server_id: str, tool_name: str, args: dict[str, object]) -> MagicMock:
        result = MagicMock()
        if tool_name in {"put_page", "add_tag", "add_link"}:
            result.data = [MagicMock(text='{"ok": true}')]
        elif tool_name == "query":
            results = [
                {
                    "slug": "memory-alice-20260528",
                    "content": "user: alice\n# grief\nKatha helped",
                    "score": 0.85,
                },
                {
                    "slug": "concepts/karma",
                    "content": "Karma is action",
                    "score": 0.6,
                },
            ]
            result.data = [MagicMock(text=json.dumps({"results": results}))]
        elif tool_name == "extract_facts":
            result.data = [MagicMock(text='{"inserted": 2, "deduplicated": 0}')]
        elif tool_name == "recall":
            facts = [
                {"id": 1, "text": "user prefers Katha Upanishad"},
                {"id": 2, "text": "user dealing with grief"},
            ]
            result.data = [MagicMock(text=json.dumps({"facts": facts}))]
        elif tool_name == "delete_page":
            result.data = [MagicMock(text='{"ok": true}')]
        elif tool_name == "list_pages":
            pages = [
                {"slug": "memory-alice-1", "type": "memory"},
                {"slug": "memory-alice-2", "type": "memory"},
            ]
            result.data = [MagicMock(text=json.dumps({"pages": pages}))]
        else:
            result.data = [MagicMock(text='{"ok": true}')]
        return result

    mcp.call_tool = AsyncMock(side_effect=call_tool)
    return mcp


@pytest.fixture()
def gbrain(mock_mcp: AsyncMock) -> GBrainMemory:
    return GBrainMemory(mock_mcp, server_id="gbrain")


# ---------------------------------------------------------------------------
# save_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_memory(gbrain: GBrainMemory, mock_mcp: AsyncMock) -> None:
    slug = await gbrain.save_memory(
        topic="Grief discussion",
        context="User lost a friend",
        key_points=["Katha 2.19 resonated"],
        tags=["grief", "loss"],
        user="alice",
        session_id="conv-12",
        agent_id="advisor",
    )

    assert slug.startswith("memory-alice-")

    calls = mock_mcp.call_tool.call_args_list
    tool_names = [c.args[1] for c in calls]
    assert "put_page" in tool_names
    assert tool_names.count("add_tag") == 2


@pytest.mark.asyncio
async def test_save_memory_with_related_sessions(gbrain: GBrainMemory, mock_mcp: AsyncMock) -> None:
    await gbrain.save_memory(
        topic="Letting go",
        tags=["attachment"],
        user="alice",
        session_id="conv-28",
        related_sessions=["conv-12"],
    )

    calls = mock_mcp.call_tool.call_args_list
    tool_names = [c.args[1] for c in calls]
    assert "add_link" in tool_names


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_memory(gbrain: GBrainMemory) -> None:
    results = await gbrain.search_memory("grief", user="alice")
    assert len(results) == 1
    assert results[0]["slug"].startswith("memory-")
    assert results[0]["score"] == 0.85


@pytest.mark.asyncio
async def test_search_memory_filters_non_memory(
    gbrain: GBrainMemory,
) -> None:
    results = await gbrain.search_memory("karma")
    assert all(r["slug"].startswith("memory") for r in results)


# ---------------------------------------------------------------------------
# extract_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_facts(gbrain: GBrainMemory) -> None:
    result = await gbrain.extract_facts(
        "I found comfort in the Katha Upanishad's teaching on impermanence",
        session_id="conv-12",
    )
    assert result["inserted"] == 2


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall(gbrain: GBrainMemory) -> None:
    result = await gbrain.recall(session_id="conv-12", limit=10)
    assert "facts" in result
    assert len(result["facts"]) == 2


# ---------------------------------------------------------------------------
# delete + list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_memory(gbrain: GBrainMemory) -> None:
    result = await gbrain.delete_memory("memory-alice-20260528")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_list_memories(gbrain: GBrainMemory) -> None:
    pages = await gbrain.list_memories(user="alice")
    assert len(pages) == 2
