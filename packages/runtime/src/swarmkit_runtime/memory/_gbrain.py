"""GBrain-backed workspace memory.

Delegates to GBrain MCP tools for storage, search, and fact extraction.
GBrain provides:
- `put_page` / `get_page` / `list_pages` for structured memory pages
- `query` for hybrid vector + keyword search
- `extract_facts` for LLM-based fact extraction from conversation turns
- `recall` for per-entity hot memory retrieval
- `add_link` / `get_links` for cross-session relationships
- `add_tag` for semantic tagging

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("swarmkit.memory.gbrain")

MEMORY_PAGE_TYPE = "memory"
MEMORY_TAG_PREFIX = "memory:"


def _build_memory_page(
    *,
    slug: str,
    topic: str,
    context: str,
    key_points: list[str],
    tags: list[str],
    user: str | None,
    session_id: str | None,
    agent_id: str | None,
) -> str:
    """Build a GBrain page in markdown with frontmatter."""
    frontmatter_lines = [
        "---",
        f"type: {MEMORY_PAGE_TYPE}",
        f"created: {datetime.now(UTC).isoformat()}",
    ]
    if user:
        frontmatter_lines.append(f"user: {user}")
    if session_id:
        frontmatter_lines.append(f"session: {session_id}")
    if agent_id:
        frontmatter_lines.append(f"agent: {agent_id}")
    if tags:
        frontmatter_lines.append(f"tags: [{', '.join(tags)}]")
    frontmatter_lines.append("---")

    body_lines = [f"# {topic}", ""]
    if context:
        body_lines.extend([context, ""])
    if key_points:
        body_lines.append("## Key Points")
        for kp in key_points:
            body_lines.append(f"- {kp}")

    return "\n".join([*frontmatter_lines, "", *body_lines])


class GBrainMemory:
    """Workspace memory backed by a GBrain MCP server.

    Requires an MCP client manager with a ``gbrain`` server configured.
    All operations are async and go through MCP tool calls.

    Parameters
    ----------
    mcp_manager:
        The workspace's MCPClientManager instance.
    server_id:
        GBrain server ID in the workspace MCP config. Defaults to ``"gbrain"``.
    """

    def __init__(
        self,
        mcp_manager: Any,
        server_id: str = "gbrain",
    ) -> None:
        self._mcp = mcp_manager
        self._server = server_id

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        """Call a GBrain MCP tool and return the result."""
        result = await self._mcp.call_tool(self._server, tool, args)
        raw = result.data if hasattr(result, "data") else result
        if isinstance(raw, list) and raw:
            text = raw[0].text if hasattr(raw[0], "text") else str(raw[0])
        elif isinstance(raw, str):
            text = raw
        else:
            text = str(raw)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    async def save_memory(
        self,
        *,
        topic: str,
        context: str = "",
        key_points: list[str] | None = None,
        tags: list[str] | None = None,
        user: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        related_sessions: list[str] | None = None,
    ) -> str:
        """Save a memory entry as a GBrain page.

        Returns the page slug.
        """
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        slug_parts = ["memory"]
        if user:
            slug_parts.append(user)
        slug_parts.append(ts)
        slug = "/".join(slug_parts)

        content = _build_memory_page(
            slug=slug,
            topic=topic,
            context=context,
            key_points=key_points or [],
            tags=tags or [],
            user=user,
            session_id=session_id,
            agent_id=agent_id,
        )

        await self._call("put_page", {"slug": slug, "content": content})

        for tag in tags or []:
            await self._call("add_tag", {"slug": slug, "tag": f"{MEMORY_TAG_PREFIX}{tag}"})

        if related_sessions:
            for related in related_sessions:
                await self._call(
                    "add_link",
                    {
                        "from": slug,
                        "to": f"memory/{related}",
                        "link_type": "related_session",
                        "context": f"Cross-session link from {session_id}",
                    },
                )

        logger.info(
            "GBrain memory saved: slug=%s topic=%r user=%s",
            slug,
            topic,
            user,
        )
        return slug

    async def search_memory(
        self,
        query: str,
        *,
        user: str | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory using GBrain hybrid search.

        Returns a list of matching memory entries with their content.
        """
        result = await self._call(
            "query",
            {
                "query": query,
                "limit": max_results,
            },
        )

        entries: list[dict[str, Any]] = []
        if isinstance(result, dict):
            results_list = result.get("results", [])
        elif isinstance(result, list):
            results_list = result
        else:
            return []

        for item in results_list:
            if isinstance(item, dict):
                slug = item.get("slug", "")
                if not slug.startswith("memory"):
                    continue
                if user and f"user: {user}" not in item.get("content", ""):
                    continue
                entries.append(
                    {
                        "slug": slug,
                        "content": item.get("content", ""),
                        "score": item.get("score", 0),
                    }
                )

        return entries[:max_results]

    async def extract_facts(
        self,
        turn_text: str,
        *,
        session_id: str | None = None,
    ) -> Any:
        """Use GBrain's built-in fact extraction.

        Delegates to `extract_facts` MCP tool which handles LLM
        extraction, deduplication, and storage internally.
        """
        args: dict[str, Any] = {"turn_text": turn_text}
        if session_id:
            args["session_id"] = session_id
        return await self._call("extract_facts", args)

    async def recall(
        self,
        *,
        entity: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> Any:
        """Query GBrain's per-source hot memory (facts table).

        Returns recent facts, optionally filtered by entity or session.
        """
        args: dict[str, Any] = {"limit": limit}
        if entity:
            args["entity"] = entity
        if session_id:
            args["session_id"] = session_id
        if since:
            args["since"] = since
        return await self._call("recall", args)

    async def get_links(self, slug: str) -> Any:
        """Get outgoing links from a memory page."""
        return await self._call("get_links", {"slug": slug})

    async def delete_memory(self, slug: str) -> Any:
        """Soft-delete a memory page."""
        return await self._call("delete_page", {"slug": slug})

    async def list_memories(
        self,
        *,
        user: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List memory pages, optionally filtered by user tag."""
        args: dict[str, Any] = {
            "type": MEMORY_PAGE_TYPE,
            "limit": limit,
            "sort": "updated_desc",
        }
        if user:
            args["tag"] = f"{MEMORY_TAG_PREFIX}user:{user}"

        result = await self._call("list_pages", args)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return list(result.get("pages", []))
        return []


__all__ = ["GBrainMemory"]
