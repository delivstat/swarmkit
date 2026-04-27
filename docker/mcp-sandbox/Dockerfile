# SwarmKit MCP Sandbox — base image for sandboxed MCP servers (design §8.8).
#
# Pre-installs the mcp SDK so swarm-authored servers work inside the
# Docker sandbox without network access (--network=none).
#
# Build:  docker build -t swael-mcp-sandbox docker/mcp-sandbox/
# Or:     just build-sandbox-image

FROM python:3.11-slim

RUN pip install --no-cache-dir mcp pydantic pyyaml httpx

WORKDIR /workspace
