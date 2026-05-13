"""Tests for ``MCPClientManager`` + ``parse_mcp_servers``.

Covers the schema-shape adapter and env-var resolution, not real
subprocess startup. End-to-end execution is exercised through
``test_hello_swarm_example`` and ``just demo-run``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from swarmkit_runtime.mcp import MCPClientManager, MCPServerConfig, parse_mcp_servers
from swarmkit_runtime.mcp._client import (
    _build_sandboxed_command,
    _resolve_env,
    collect_required_servers,
)
from swarmkit_schema.models.workspace import McpServer, Transport


def _stdio(server_id: str, command: list[str], **extra: object) -> McpServer:
    return McpServer.model_validate(
        {
            "id": server_id,
            "transport": "stdio",
            "command": command,
            **extra,
        }
    )


def _http(server_id: str, endpoint: str, **extra: object) -> McpServer:
    return McpServer.model_validate(
        {
            "id": server_id,
            "transport": "http",
            "endpoint": endpoint,
            **extra,
        }
    )


# ---- parse_mcp_servers -------------------------------------------------


def test_parse_returns_empty_for_none() -> None:
    assert parse_mcp_servers(None) == {}


def test_parse_returns_empty_for_empty_list() -> None:
    assert parse_mcp_servers([]) == {}


def test_parse_stdio_entry() -> None:
    configs = parse_mcp_servers([_stdio("hello-world", ["python", "server.py"])])
    assert set(configs) == {"hello-world"}
    cfg = configs["hello-world"]
    assert cfg.transport == "stdio"
    assert cfg.command == ["python", "server.py"]
    assert cfg.endpoint == ""
    assert cfg.env is None


def test_parse_http_entry() -> None:
    configs = parse_mcp_servers([_http("rynko", "https://mcp.example.com")])
    cfg = configs["rynko"]
    assert cfg.transport == "http"
    assert cfg.endpoint == "https://mcp.example.com"
    assert cfg.command == []


def test_parse_preserves_env_dict() -> None:
    configs = parse_mcp_servers(
        [_stdio("qdrant", ["uvx", "mcp-server-qdrant"], env={"QDRANT_URL": "http://x"})]
    )
    assert configs["qdrant"].env == {"QDRANT_URL": "http://x"}


def test_parse_handles_multiple_servers() -> None:
    configs = parse_mcp_servers(
        [
            _stdio("a", ["python", "a.py"]),
            _http("b", "https://b.example.com"),
        ]
    )
    assert set(configs) == {"a", "b"}
    assert configs["a"].transport == "stdio"
    assert configs["b"].transport == "http"


def test_parse_accepts_typed_transport_enum() -> None:
    """Pydantic gives us ``Transport.stdio`` / ``Transport.http`` enums.

    The parser must compare by ``.value``, not against the string literal,
    because the schema-codegen pydantic model stores ``Transport``.
    """
    server = _http("enum-server", "https://example.com")
    server.transport = Transport.http
    cfg = parse_mcp_servers([server])["enum-server"]
    assert cfg.transport == "http"


# ---- _resolve_env -------------------------------------------------------


def test_resolve_env_expands_var_reference(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SWARMKIT_TEST_TOKEN", "secret")
    resolved = _resolve_env({"TOKEN": "${SWARMKIT_TEST_TOKEN}", "MODE": "prod"})
    assert resolved is not None
    assert resolved["TOKEN"] == "secret"
    assert resolved["MODE"] == "prod"
    assert "PATH" in resolved  # inherits parent env


def test_resolve_env_unknown_var_becomes_empty_string(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SWARMKIT_TEST_MISSING", raising=False)
    resolved = _resolve_env({"X": "${SWARMKIT_TEST_MISSING}"})
    assert resolved is not None
    assert resolved["X"] == ""


def test_resolve_env_expands_embedded_var(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SWARMKIT_TEST_BASE", "/data/knowledge")
    resolved = _resolve_env({"DB": "${SWARMKIT_TEST_BASE}/chromadb"})
    assert resolved is not None
    assert resolved["DB"] == "/data/knowledge/chromadb"


def test_resolve_env_expands_multiple_vars(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SWARMKIT_TEST_HOST", "localhost")
    monkeypatch.setenv("SWARMKIT_TEST_PORT", "8080")
    resolved = _resolve_env({"URL": "${SWARMKIT_TEST_HOST}:${SWARMKIT_TEST_PORT}/api"})
    assert resolved is not None
    assert resolved["URL"] == "localhost:8080/api"


def test_resolve_env_returns_none_for_empty() -> None:
    assert _resolve_env(None) is None
    assert _resolve_env({}) is None


# ---- MCPClientManager construction -------------------------------------


def test_manager_server_ids_sorted() -> None:
    configs = parse_mcp_servers([_stdio("zeta", ["x"]), _stdio("alpha", ["y"])])
    manager = MCPClientManager(configs)
    assert manager.server_ids == ["alpha", "zeta"]


def test_manager_empty_when_no_configs() -> None:
    manager = MCPClientManager()
    assert manager.server_ids == []


# ---- sandboxed server support -------------------------------------------


def test_parse_sandboxed_flag_preserved() -> None:
    configs = parse_mcp_servers(
        [_stdio("sandboxed-server", ["python", "server.py"], sandboxed=True)]
    )
    assert configs["sandboxed-server"].sandboxed is True


def test_parse_sandboxed_default_false() -> None:
    configs = parse_mcp_servers([_stdio("normal", ["python", "server.py"])])
    assert configs["normal"].sandboxed is False


def test_build_sandboxed_command_wraps_in_docker() -> None:
    config = MCPServerConfig(
        server_id="test",
        command=["python", "server.py"],
        sandboxed=True,
    )
    cmd, args, env = _build_sandboxed_command(config, workspace_root=Path("/tmp/test-workspace"))
    assert cmd == "docker"
    assert "run" in args
    assert "-i" in args
    assert "--rm" in args
    assert "--network=none" in args
    assert "-v" in args
    vol_idx = args.index("-v")
    assert "/tmp/test-workspace:/workspace:ro" in args[vol_idx + 1]
    assert "python" in args
    assert "server.py" in args
    assert env is None


def test_build_sandboxed_command_passes_env_via_docker_e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_KEY", "test_val")
    config = MCPServerConfig(
        server_id="test",
        command=["python", "server.py"],
        env={"API_KEY": "${TEST_KEY}"},
        sandboxed=True,
    )
    _, args, _ = _build_sandboxed_command(config)
    assert "-e" in args
    e_idx = args.index("-e")
    assert args[e_idx + 1] == "API_KEY=test_val"


def test_build_sandboxed_command_no_workspace() -> None:
    config = MCPServerConfig(
        server_id="test",
        command=["python", "server.py"],
        sandboxed=True,
    )
    _, args, _ = _build_sandboxed_command(config, workspace_root=None)
    assert "-v" not in args
    assert "-w" not in args


# ---- collect_required_servers -------------------------------------------


def _make_skill(
    impl_type: str, server: str | None = None, tool: str | None = None
) -> SimpleNamespace:
    impl: dict[str, str] = {"type": impl_type}
    if server:
        impl["server"] = server
    if tool:
        impl["tool"] = tool
    return SimpleNamespace(raw=SimpleNamespace(implementation=impl))


def _make_agent(
    agent_id: str,
    skills: list[SimpleNamespace],
    children: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        skills=tuple(skills),
        children=tuple(children or []),
    )


def _make_topology(root: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(root=root)


def test_collect_required_servers_single_mcp_tool() -> None:
    skill = _make_skill("mcp_tool", server="github", tool="get_pr")
    root = _make_agent("root", [skill])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == {"github"}


def test_collect_required_servers_ignores_non_mcp_skills() -> None:
    s1 = _make_skill("llm_prompt")
    s2 = _make_skill("mcp_tool", server="github", tool="get_pr")
    root = _make_agent("root", [s1, s2])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == {"github"}


def test_collect_required_servers_walks_children() -> None:
    child1 = _make_agent("reader", [_make_skill("mcp_tool", server="github", tool="get_pr")])
    child2 = _make_agent("searcher", [_make_skill("mcp_tool", server="chromadb", tool="search")])
    root = _make_agent("supervisor", [_make_skill("llm_prompt")], [child1, child2])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == {"github", "chromadb"}


def test_collect_required_servers_deduplicates() -> None:
    s1 = _make_skill("mcp_tool", server="github", tool="get_pr")
    s2 = _make_skill("mcp_tool", server="github", tool="list_repos")
    root = _make_agent("root", [s1, s2])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == {"github"}


def test_collect_required_servers_empty_for_llm_only() -> None:
    root = _make_agent("root", [_make_skill("llm_prompt")])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == set()


def test_collect_required_servers_deep_nesting() -> None:
    leaf = _make_agent("leaf", [_make_skill("mcp_tool", server="deep-server", tool="x")])
    mid = _make_agent("mid", [_make_skill("llm_prompt")], [leaf])
    root = _make_agent("root", [_make_skill("llm_prompt")], [mid])
    topo = _make_topology(root)
    assert collect_required_servers(topo) == {"deep-server"}


# ---- permission tiers ----------------------------------------------------


def test_parse_permission_default_cautious() -> None:
    configs = parse_mcp_servers([_stdio("github", ["npx", "server"])])
    assert configs["github"].permission == "cautious"


def test_parse_permission_explicit() -> None:
    configs = parse_mcp_servers([_stdio("github", ["npx", "server"], permission="strict")])
    assert configs["github"].permission == "strict"


def test_parse_permission_overrides() -> None:
    configs = parse_mcp_servers(
        [
            _stdio(
                "github",
                ["npx", "server"],
                permission="cautious",
                permission_overrides={"delete_branch": "strict", "get_pr": "open"},
            )
        ]
    )
    cfg = configs["github"]
    assert cfg.permission == "cautious"
    assert cfg.permission_overrides == {"delete_branch": "strict", "get_pr": "open"}


def test_manager_get_permission_server_default() -> None:
    configs = {
        "github": MCPServerConfig(server_id="github", permission="strict"),
    }
    manager = MCPClientManager(configs)
    assert manager.get_permission("github", "any_tool") == "strict"


def test_manager_get_permission_tool_override() -> None:
    configs = {
        "github": MCPServerConfig(
            server_id="github",
            permission="cautious",
            permission_overrides={"delete_branch": "strict"},
        ),
    }
    manager = MCPClientManager(configs)
    assert manager.get_permission("github", "get_pr") == "cautious"
    assert manager.get_permission("github", "delete_branch") == "strict"


def test_manager_get_permission_unknown_server() -> None:
    manager = MCPClientManager()
    assert manager.get_permission("unknown", "tool") == "cautious"
