# /// script
# dependencies = ["mcp>=1.0,<2"]
# ///
"""A small weather MCP server — one stdio tool, mock data.

`uv run servers/weather_server.py` installs `mcp` from the header above on first use, so the
workspace needs no project file of its own. SwarmKit launches it as a subprocess from the
workspace's `mcp_servers` entry; run it by hand to see it speak MCP.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

server = FastMCP("weather")


@server.tool()
def get_weather(city: str) -> str:
    """Current weather for a city (mock data; a real server would call an API here)."""
    return json.dumps(
        {"city": city, "temperature": "22°C", "condition": "Partly cloudy", "humidity": "65%"}
    )


if __name__ == "__main__":
    server.run()
