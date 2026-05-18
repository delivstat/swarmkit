"""MCP integration (design §18).

See ``design/details/mcp-client.md``.
"""

from ._client import (
    MCPClientManager,
    MCPServerConfig,
    PermissionTier,
    ToolMetadata,
    ToolResponse,
    collect_required_servers,
    parse_mcp_servers,
)

__all__ = [
    "MCPClientManager",
    "MCPServerConfig",
    "PermissionTier",
    "ToolMetadata",
    "ToolResponse",
    "collect_required_servers",
    "parse_mcp_servers",
]
