"""MCP reachability from a container sandbox (executor-container-sandbox.md, task #20).

http MCP servers → hostnames to add to the egress allowlist; stdio ones can't cross the boundary
(warned, not bridged). Also covers the allowlist-merge + stdio warning in the container layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from swarmkit_runtime.executors import SandboxSpec, mcp_reachability
from swarmkit_runtime.executors._container import _effective_allow


@dataclass
class _Cfg:
    transport: str
    endpoint: str = ""


def test_extracts_http_hosts_and_flags_stdio() -> None:
    configs = {
        "docs": _Cfg("http", "https://mcp.example.com:8080/sse"),
        "search": _Cfg("http", "http://search.internal/mcp"),
        "fs": _Cfg("stdio"),
        "git": _Cfg("stdio"),
    }
    http_hosts, stdio_ids = mcp_reachability(configs)
    assert http_hosts == ["mcp.example.com", "search.internal"]  # sorted, port stripped
    assert stdio_ids == ["fs", "git"]


def test_empty_and_malformed_endpoints() -> None:
    http_hosts, stdio_ids = mcp_reachability({"bad": _Cfg("http", ""), "ok": _Cfg("stdio")})
    assert http_hosts == []  # no hostname → nothing to allow
    assert stdio_ids == ["ok"]


def test_effective_allow_merges_http_hosts_for_container_allowlist() -> None:
    spec = SandboxSpec(kind="container", network="allowlist", allow=("api.anthropic.com",))
    configs = {"docs": _Cfg("http", "https://mcp.example.com/sse")}
    allow = _effective_allow(spec, configs)
    assert allow == ("api.anthropic.com", "mcp.example.com")  # deduped union


def test_effective_allow_noop_off_container_or_deny() -> None:
    configs = {"docs": _Cfg("http", "https://mcp.example.com/sse")}
    # worktree: no merge
    assert _effective_allow(SandboxSpec(kind="worktree"), configs) == ()
    # container + deny: no allowlist to extend
    spec = SandboxSpec(kind="container", network="deny", allow=("x",))
    assert _effective_allow(spec, configs) == ("x",)


def test_stdio_mcp_with_container_allowlist_warns(caplog: logging.LogCaptureFixture) -> None:
    spec = SandboxSpec(kind="container", network="allowlist", allow=("h",))
    with caplog.at_level(logging.WARNING):
        _effective_allow(spec, {"fs": _Cfg("stdio")})
    assert "stdio MCP server" in caplog.text
    assert "fs" in caplog.text
