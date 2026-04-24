"""MCP integration (design §18).

See ``design/details/mcp-client.md``.
"""

from ._client import MCPClientManager, MCPServerConfig, parse_mcp_servers

__all__ = ["MCPClientManager", "MCPServerConfig", "parse_mcp_servers"]
