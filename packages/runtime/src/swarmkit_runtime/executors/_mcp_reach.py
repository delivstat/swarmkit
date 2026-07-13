"""MCP reachability from inside a container sandbox (executor-container-sandbox.md, task #20).

A harness in a locked-down container reaches only what we hand it. For the workspace's MCP servers:

- **http** servers are reachable by adding their hostname to the egress ``allow`` list (they speak
  over the network) — so we surface those hostnames to merge into the allowlist.
- **stdio** servers speak over pipes to a local subprocess, which a container boundary can't cross;
  v1 does not bridge them (sidecar/shim deferred), so we surface their ids to warn the operator.

Pure + duck-typed on ``.transport`` / ``.endpoint`` so this module doesn't import the MCP client
(and cannot create an import cycle).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse


def mcp_reachability(configs: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(http_hosts, stdio_ids)`` for the given MCP server configs.

    ``http_hosts`` are the hostnames to add to a container's egress allowlist; ``stdio_ids`` are
    the servers that can't be reached from inside a container (warn). Both sorted + deduped.
    """
    http_hosts: set[str] = set()
    stdio_ids: set[str] = set()
    for server_id, cfg in configs.items():
        transport = getattr(cfg, "transport", "stdio")
        if transport == "http":
            host = urlparse(getattr(cfg, "endpoint", "") or "").hostname
            if host:
                http_hosts.add(host)
        else:
            stdio_ids.add(str(server_id))
    return sorted(http_hosts), sorted(stdio_ids)


__all__ = ["mcp_reachability"]
