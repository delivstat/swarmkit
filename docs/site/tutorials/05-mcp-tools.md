# Level 5: MCP Tools

Give your agents real tools that interact with the world — files, APIs, databases, browsers.

## What you'll learn

- Configuring MCP servers in workspace.yaml
- Writing a custom MCP server in Python
- Permission tiers (open, cautious, strict, readonly)
- Sandboxed execution (Docker isolation)
- Lazy startup
- Environment variables and credentials

## What is MCP?

Model Context Protocol (MCP) is a standard for connecting AI agents to tools. Instead of building custom tool integrations, you wire existing MCP servers — there are 7,000+ available for GitHub, databases, Slack, file systems, browsers, and more.

SwarmKit skills with `type: mcp_tool` call tools on MCP servers.

## Build it

### 1. Add an MCP server to your workspace

The filesystem MCP server lets agents read and write files:

```yaml
# workspace.yaml — updated
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm
  name: My First Swarm
  description: Learning SwarmKit step by step.
governance:
  provider: mock
mcp_servers:
  - id: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
```

This starts the filesystem MCP server with access to the current directory.

### 2. Wire a skill to the MCP server

Update your `read-file` skill to target this server:

```yaml
# skills/read-file.yaml — already created in Level 3
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: read-file
  name: Read File
  description: Read the contents of a file from the workspace.
category: capability
implementation:
  type: mcp_tool
  server: filesystem      # matches the id in workspace.yaml
  tool: read_file         # the tool name exposed by the MCP server
provenance:
  authored_by: human
  version: 1.0.0
```

### 3. Write a custom MCP server

Create a simple MCP server that provides a weather lookup tool:

```python
# servers/weather_server.py
"""Simple weather MCP server — returns mock weather data."""

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
import asyncio
import json

server = Server("weather")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_weather",
            description="Get the current weather for a city.",
            inputSchema={
                "type": "object",
                "required": ["city"],
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name (e.g., Tokyo, London)",
                    },
                },
            },
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "get_weather":
        city = arguments.get("city", "Unknown")
        # In production, call a real weather API here
        weather = {
            "city": city,
            "temperature": "22°C",
            "condition": "Partly cloudy",
            "humidity": "65%",
        }
        return [TextContent(type="text", text=json.dumps(weather))]
    return [TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

Register it in workspace.yaml:

```yaml
mcp_servers:
  - id: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]

  - id: weather
    transport: stdio
    command: ["uv", "run", "servers/weather_server.py"]
```

Create the skill:

```yaml
# skills/get-weather.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: get-weather
  name: Get Weather
  description: Get current weather for any city.
category: capability
implementation:
  type: mcp_tool
  server: weather
  tool: get_weather
provenance:
  authored_by: human
  version: 1.0.0
```

Add the skill to your assistant archetype:

```yaml
# archetypes/friendly-assistant.yaml — updated skills
  skills:
    - read-file
    - summarize
    - get-weather
```

### 4. Permission tiers

Control what MCP servers can do:

```yaml
mcp_servers:
  - id: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
    permission: readonly    # agents can read but not write files

  - id: weather
    transport: stdio
    command: ["uv", "run", "servers/weather_server.py"]
    permission: open        # no governance check needed

  - id: database
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-postgres"]
    permission: strict      # every call requires governance approval
    permission_overrides:
      list_tables: open     # except listing tables — that's safe
```

| Tier | Behavior |
|------|----------|
| `open` | Skip governance — fast, no approval needed |
| `cautious` (default) | Reads auto-approved, writes need governance |
| `strict` | Every call requires governance approval |
| `readonly` | Deny all write operations |

### 5. Sandboxed execution

For untrusted MCP servers, run them in Docker:

```yaml
mcp_servers:
  - id: untrusted-tool
    transport: stdio
    command: ["python", "some_tool.py"]
    sandboxed: true         # runs in Docker container
    sandbox_image: python:3.11-slim  # optional custom image
```

Sandboxed servers run with `--network=none` (no internet) and the workspace mounted read-only at `/workspace`.

### 6. Environment variables and credentials

```yaml
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"

credentials:
  github-token:
    source: env
    config:
      env: GITHUB_TOKEN
```

Environment variables use `${VAR_NAME}` interpolation — resolved at runtime from your shell environment.

### 7. Test it

```bash
# Create a test file
echo "Hello from SwarmKit!" > test-file.txt

# Run the assistant and ask it to read the file
swarmkit run . hello --input "Read the file test-file.txt and tell me what it says"

# Ask about weather
swarmkit run . hello --input "What's the weather in Tokyo?"
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml          # now has mcp_servers config
├── archetypes/
├── skills/
│   ├── read-file.yaml
│   ├── get-weather.yaml    # new
│   └── ...
├── servers/
│   └── weather_server.py   # custom MCP server
└── topologies/
```

## Next

[Level 6: Structured Delegation](06-structured-delegation.md) — task plans, scopes, and the dual model pattern.
