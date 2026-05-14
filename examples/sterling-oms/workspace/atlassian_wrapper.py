# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Atlassian wrapper MCP server.

Provides structured-input tools that generate valid JQL/CQL internally,
then proxy to the real mcp-atlassian server. Models never write query
syntax — they pass structured fields and keywords.
"""

from __future__ import annotations

import json
import os
import subprocess

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("atlassian-wrapper")

# ---- JQL / CQL builders ---------------------------------------------------


def _build_jql(
    keywords: list[str],
    project: str | None = None,
    issue_type: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    created_after: str | None = None,
    updated_after: str | None = None,
    limit: int = 20,
) -> str:
    """Build a valid JQL query from structured fields."""
    clauses: list[str] = []

    if project:
        clauses.append(f'project = "{project}"')
    if issue_type:
        clauses.append(f'issuetype = "{issue_type}"')
    if status:
        clauses.append(f'status = "{status}"')
    if assignee:
        clauses.append(f'assignee = "{assignee}"')
    if labels:
        label_parts = ", ".join(f'"{lbl}"' for lbl in labels)
        clauses.append(f"labels IN ({label_parts})")
    if created_after:
        clauses.append(f'created >= "{created_after}"')
    if updated_after:
        clauses.append(f'updated >= "{updated_after}"')

    if keywords:
        if len(keywords) == 1:
            clauses.append(f'text ~ "{keywords[0]}"')
        else:
            kw_parts = " OR ".join(f'text ~ "{kw}"' for kw in keywords)
            clauses.append(f"({kw_parts})")

    if not clauses:
        return "ORDER BY created DESC"

    return " AND ".join(clauses) + " ORDER BY created DESC"


def _build_cql(
    keywords: list[str],
    space_key: str | None = None,
    content_type: str | None = None,
    label: str | None = None,
) -> str:
    """Build a valid CQL query from structured fields."""
    parts: list[str] = []

    if space_key:
        parts.append(f'space = "{space_key}"')
    if content_type:
        parts.append(f'type = "{content_type}"')
    if label:
        parts.append(f'label = "{label}"')

    if keywords:
        text = " ".join(keywords)
        parts.append(f'text ~ "{text}"')

    return " AND ".join(parts) if parts else ""


# ---- tool definitions ------------------------------------------------------


TOOLS = [
    Tool(
        name="search_jira",
        description=(
            "Search Jira issues using structured filters. Pass keywords as "
            "an array of strings — the tool builds valid JQL internally. "
            "Returns matching issues with key, summary, status, and assignee."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Search terms (e.g. ['return', 'RETN', 'RITN']). "
                        "Each becomes a text ~ search joined with OR."
                    ),
                },
                "project": {
                    "type": "string",
                    "description": "Jira project key (e.g. 'CROMA'). Optional.",
                },
                "issue_type": {
                    "type": "string",
                    "description": "Issue type filter (e.g. 'Bug', 'Story', 'Task'). Optional.",
                },
                "status": {
                    "type": "string",
                    "description": "Status filter (e.g. 'In Progress', 'Done'). Optional.",
                },
                "assignee": {
                    "type": "string",
                    "description": "Assignee filter. Optional.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Label filters. Optional.",
                },
                "created_after": {
                    "type": "string",
                    "description": "Created after date (YYYY-MM-DD). Optional.",
                },
                "updated_after": {
                    "type": "string",
                    "description": "Updated after date (YYYY-MM-DD). Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                    "default": 20,
                },
            },
            "required": ["keywords"],
        },
    ),
    Tool(
        name="search_confluence",
        description=(
            "Search Confluence pages using structured filters. Pass keywords "
            "as an array of strings — the tool builds valid CQL internally. "
            "Returns matching pages with title, space, and excerpt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Search terms (e.g. ['return order', 'CROMA']). Joined for text search."
                    ),
                },
                "space_key": {
                    "type": "string",
                    "description": "Confluence space key to limit search. Optional.",
                },
                "content_type": {
                    "type": "string",
                    "description": "Content type: 'page' or 'blogpost'. Optional.",
                },
                "label": {
                    "type": "string",
                    "description": "Filter by label. Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                    "default": 20,
                },
            },
            "required": ["keywords"],
        },
    ),
    Tool(
        name="get_confluence_page",
        description=(
            "Get a Confluence page by ID or by title + space. "
            "Use page_id from search results when available."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Confluence page ID (from search results). Preferred.",
                },
                "title": {
                    "type": "string",
                    "description": "Page title. Must also provide space_key.",
                },
                "space_key": {
                    "type": "string",
                    "description": "Confluence space key. Required with title.",
                },
            },
        },
    ),
    Tool(
        name="get_jira_issue",
        description=(
            "Get full details of a Jira issue by key (e.g. 'RT-727'). "
            "Returns summary, description, status, comments, and linked issues."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "Jira issue key (e.g. 'RT-727').",
                },
                "comment_limit": {
                    "type": "integer",
                    "description": "Max comments to include (default 50).",
                    "default": 50,
                },
            },
            "required": ["issue_key"],
        },
    ),
    Tool(
        name="get_jira_attachments",
        description="Download and read attachments from a Jira issue.",
        inputSchema={
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "Jira issue key (e.g. 'RT-727').",
                },
            },
            "required": ["issue_key"],
        },
    ),
    Tool(
        name="get_confluence_attachments",
        description="Get attachments from a Confluence page.",
        inputSchema={
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Confluence page ID.",
                },
            },
            "required": ["page_id"],
        },
    ),
]

# ---- MCP client to real atlassian server -----------------------------------

_atlassian_process: subprocess.Popen | None = None  # type: ignore[type-arg]
_request_id = 0


def _get_atlassian() -> subprocess.Popen:  # type: ignore[type-arg]
    """Start the real mcp-atlassian server as a subprocess."""
    global _atlassian_process  # noqa: PLW0603
    if _atlassian_process is not None and _atlassian_process.poll() is None:
        return _atlassian_process

    cmd = [
        "uvx",
        "mcp-atlassian",
        "--confluence-url",
        os.environ.get("CONFLUENCE_URL", ""),
        "--confluence-username",
        os.environ.get("ATLASSIAN_USERNAME", ""),
        "--confluence-token",
        os.environ.get("ATLASSIAN_API_TOKEN", ""),
        "--jira-url",
        os.environ.get("JIRA_URL", ""),
        "--jira-username",
        os.environ.get("ATLASSIAN_USERNAME", ""),
        "--jira-token",
        os.environ.get("ATLASSIAN_API_TOKEN", ""),
    ]
    _atlassian_process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    _send_jsonrpc(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "atlassian-wrapper", "version": "1.0"},
        },
    )
    return _atlassian_process


def _send_jsonrpc(method: str, params: dict) -> dict:  # type: ignore[type-arg]
    """Send a JSON-RPC request to the mcp-atlassian subprocess."""
    global _request_id  # noqa: PLW0603
    _request_id += 1
    proc = _get_atlassian()
    assert proc.stdin is not None
    assert proc.stdout is not None

    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": _request_id,
                "method": method,
                "params": params,
            }
        )
        + "\n"
    )
    proc.stdin.write(request.encode())
    proc.stdin.flush()

    line = proc.stdout.readline().decode()
    if not line:
        return {"error": "No response from atlassian server"}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON response: {line[:200]}"}


def _call_atlassian_tool(tool_name: str, arguments: dict) -> str:  # type: ignore[type-arg]
    """Call a tool on the real mcp-atlassian server."""
    response = _send_jsonrpc(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments,
        },
    )
    if "error" in response:
        return f"Error: {response['error']}"
    result = response.get("result", {})
    content = result.get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(result)


# ---- tool handlers ---------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:  # type: ignore[type-arg]
    try:
        result = _dispatch(name, arguments)
    except Exception as exc:
        result = f"Error: {exc}"
    return [TextContent(type="text", text=result)]


def _dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]  # noqa: PLR0911
    if name == "search_jira":
        jql = _build_jql(
            keywords=args.get("keywords", []),
            project=args.get("project"),
            issue_type=args.get("issue_type"),
            status=args.get("status"),
            assignee=args.get("assignee"),
            labels=args.get("labels"),
            created_after=args.get("created_after"),
            updated_after=args.get("updated_after"),
        )
        limit = args.get("limit", 20)
        return _call_atlassian_tool("jira_search", {"jql": jql, "limit": limit})

    if name == "search_confluence":
        cql = _build_cql(
            keywords=args.get("keywords", []),
            space_key=args.get("space_key"),
            content_type=args.get("content_type"),
            label=args.get("label"),
        )
        limit = args.get("limit", 20)
        return _call_atlassian_tool("confluence_search", {"query": cql, "limit": limit})

    if name == "get_confluence_page":
        page_id = args.get("page_id")
        title = args.get("title")
        space_key = args.get("space_key")
        if page_id:
            return _call_atlassian_tool("confluence_get_page", {"page_id": page_id})
        if title and space_key:
            return _call_atlassian_tool(
                "confluence_get_page",
                {
                    "title": title,
                    "space_key": space_key,
                },
            )
        return "Error: provide page_id, or both title and space_key"

    if name == "get_jira_issue":
        return _call_atlassian_tool(
            "jira_get_issue",
            {
                "issue_key": args["issue_key"],
                "comment_limit": args.get("comment_limit", 50),
            },
        )

    if name == "get_jira_attachments":
        return _call_atlassian_tool(
            "jira_download_attachments",
            {
                "issue_key": args["issue_key"],
            },
        )

    if name == "get_confluence_attachments":
        return _call_atlassian_tool(
            "confluence_download_content_attachments",
            {
                "page_id": args["page_id"],
            },
        )

    return f"Unknown tool: {name}"


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
