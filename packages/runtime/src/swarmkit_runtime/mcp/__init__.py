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
from ._governed import MCPCallDenied, check_mcp_permission, governed_mcp_call

__all__ = [
    "MCPCallDenied",
    "MCPClientManager",
    "MCPServerConfig",
    "PermissionTier",
    "ToolMetadata",
    "ToolResponse",
    "check_mcp_permission",
    "collect_required_servers",
    "governed_mcp_call",
    "parse_mcp_servers",
]
