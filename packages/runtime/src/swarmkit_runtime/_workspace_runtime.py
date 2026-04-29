"""WorkspaceRuntime — the backend that CLI, HTTP server, and web UI call into.

Owns the full execution lifecycle: resolve workspace → build providers →
build governance → wire MCP → compile topology → invoke graph → close.
The CLI is a thin interface over this; ``swarmkit serve`` (M9) and the
v1.1 web UI will be additional interfaces over the same class.

See ``design/details/workspace-runtime.md`` (to be written) and the
architectural decision in ``memory/feedback_cli_architecture.md``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.mcp import MCPClientManager, MCPServerConfig, parse_mcp_servers
from swarmkit_runtime.model_providers import (
    MockModelProvider,
    ProviderRegistry,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.resolver import ResolvedWorkspace, resolve_workspace


@dataclass(frozen=True)
class RunEvent:
    """A single event from a topology execution."""

    event_type: str
    agent_id: str
    timestamp: str
    payload: dict[str, object] = field(default_factory=dict)
    skill_id: str | None = None


@dataclass(frozen=True)
class RunResult:
    """Output of a topology execution."""

    output: str
    agent_results: dict[str, str] = field(default_factory=dict)
    events: list[RunEvent] = field(default_factory=list)


class MissingMCPServerError(Exception):
    """A skill targets an MCP server that the workspace doesn't declare."""

    def __init__(self, missing: list[tuple[str, str]]) -> None:
        self.missing = missing
        lines = [
            f"skill '{sid}' targets MCP server '{srv}' but the workspace declares no such server"
            for sid, srv in missing
        ]
        super().__init__("\n".join(lines))


class WorkspaceRuntime:
    """The backend that both CLI and HTTP server call into.

    Holds a resolved workspace plus all the wired runtime components
    (model providers, governance, MCP manager). Constructed via the
    ``from_workspace_path`` classmethod.
    """

    def __init__(
        self,
        *,
        workspace: ResolvedWorkspace,
        workspace_root: Path,
        provider_registry: ProviderRegistry,
        governance: GovernanceProvider,
        mcp_manager: MCPClientManager | None,
    ) -> None:
        self._workspace = workspace
        self._workspace_root = workspace_root
        self._provider_registry = provider_registry
        self._governance = governance
        self._mcp_manager = mcp_manager

    @classmethod
    def from_workspace_path(cls, path: Path) -> WorkspaceRuntime:
        """Build a fully-wired runtime from a workspace directory.

        Resolves the workspace, registers model providers, selects the
        governance provider, parses MCP server config, and validates
        that every mcp_tool skill targets a configured server.

        Raises ``ResolutionErrors`` if the workspace is invalid, or
        ``MissingMCPServerError`` if skills reference unconfigured
        MCP servers.
        """
        ws_root = path.resolve()
        workspace = resolve_workspace(ws_root)

        registry = ProviderRegistry()
        register_available_providers(registry)

        governance = build_governance(workspace, ws_root)

        mcp_configs = parse_mcp_servers(getattr(workspace.raw, "mcp_servers", None))
        mcp_manager = MCPClientManager(mcp_configs, workspace_root=ws_root) if mcp_configs else None

        missing = find_missing_mcp_servers(workspace, mcp_configs)
        if missing:
            raise MissingMCPServerError(missing)

        return cls(
            workspace=workspace,
            workspace_root=ws_root,
            provider_registry=registry,
            governance=governance,
            mcp_manager=mcp_manager,
        )

    def compile(self, topology_name: str) -> CompiledStateGraph[Any]:
        """Compile a named topology into a LangGraph graph.

        Raises ``KeyError`` if the topology doesn't exist.
        """
        if topology_name not in self._workspace.topologies:
            available = sorted(self._workspace.topologies.keys())
            raise KeyError(
                f"Topology '{topology_name}' not found. "
                f"Available: {', '.join(available) or '(none)'}."
            )

        topology = self._workspace.topologies[topology_name]
        return compile_topology(
            topology,
            provider_registry=self._provider_registry,
            governance=self._governance,
            mcp_manager=self._mcp_manager,
        )

    async def run(
        self,
        topology_name: str,
        user_input: str,
        *,
        max_steps: int = 10,
    ) -> RunResult:
        """Execute a topology end-to-end and return the result.

        Handles MCP session lifecycle (start_all / close_all) within
        the same async task so the SDK's anyio task groups unwind cleanly.
        """
        graph = self.compile(topology_name)

        if self._mcp_manager is not None:
            await self._mcp_manager.start_all()
        try:
            result = await graph.ainvoke(
                {
                    "input": user_input,
                    "messages": [],
                    "agent_results": {},
                    "current_agent": "",
                    "output": "",
                },
                config={"recursion_limit": max_steps},
            )
        finally:
            if self._mcp_manager is not None:
                await self._mcp_manager.close_all()

        events = _extract_events(self._governance)

        return RunResult(
            output=result.get("output", ""),
            agent_results={
                k: str(v) for k, v in result.get("agent_results", {}).items() if isinstance(v, str)
            },
            events=events,
        )

    async def close(self) -> None:
        """Release all held resources."""
        if self._mcp_manager is not None:
            await self._mcp_manager.close_all()

    @property
    def workspace(self) -> ResolvedWorkspace:
        return self._workspace

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def governance(self) -> GovernanceProvider:
        return self._governance

    @property
    def mcp_manager(self) -> MCPClientManager | None:
        return self._mcp_manager

    @property
    def provider_registry(self) -> ProviderRegistry:
        return self._provider_registry


# ---- event extraction ----------------------------------------------------


def _extract_events(governance: GovernanceProvider) -> list[RunEvent]:
    """Pull audit events from the governance provider after a run.

    Works with MockGovernanceProvider (has .events property) and
    AGTGovernanceProvider (has .get_log() on the FlightRecorder).
    Returns empty list for providers that don't expose events.
    """
    raw_events = getattr(governance, "events", None)
    if raw_events is None:
        recorder = getattr(governance, "_recorder", None)
        if recorder is not None and hasattr(recorder, "get_log"):
            raw_events = recorder.get_log()
    if not raw_events:
        return []

    result: list[RunEvent] = []
    for evt in raw_events:
        if hasattr(evt, "event_type"):
            result.append(
                RunEvent(
                    event_type=evt.event_type,
                    agent_id=evt.agent_id,
                    timestamp=str(evt.timestamp),
                    payload=dict(evt.payload) if evt.payload else {},
                    skill_id=evt.skill_id,
                )
            )
        elif isinstance(evt, dict):
            result.append(
                RunEvent(
                    event_type=evt.get("event_type", "unknown"),
                    agent_id=evt.get("agent_id", ""),
                    timestamp=str(evt.get("timestamp", "")),
                    payload={
                        k: v
                        for k, v in evt.items()
                        if k not in {"event_type", "agent_id", "timestamp"}
                    },
                )
            )
    return result


# ---- helpers (public — used by CLI and tests) ----------------------------


def register_available_providers(registry: ProviderRegistry) -> None:
    """Register all model providers whose credentials are in the environment.

    Providers whose SDK dependencies are missing are silently skipped.
    """
    registry.register(MockModelProvider())

    _conditional = [
        ("ANTHROPIC_API_KEY", "AnthropicModelProvider"),
        ("GOOGLE_API_KEY", "GoogleModelProvider"),
        ("OPENAI_API_KEY", "OpenAIModelProvider"),
        ("OPENROUTER_API_KEY", "OpenRouterModelProvider"),
        ("GROQ_API_KEY", "GroqModelProvider"),
        ("TOGETHER_API_KEY", "TogetherModelProvider"),
    ]
    for env_var, cls_name in _conditional:
        if os.environ.get(env_var):
            try:
                from swarmkit_runtime import model_providers  # noqa: PLC0415

                cls = getattr(model_providers, cls_name)
                registry.register(cls())
            except (ImportError, ModuleNotFoundError, AttributeError):
                pass

    try:
        from swarmkit_runtime import model_providers  # noqa: PLC0415

        registry.register(model_providers.OllamaModelProvider())
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass


def build_governance(workspace: ResolvedWorkspace, ws_root: Path) -> GovernanceProvider:
    """Select the GovernanceProvider based on workspace.yaml's governance block."""
    gov = getattr(workspace.raw, "governance", None)
    if gov is None:
        return MockGovernanceProvider(allow_all=True)

    provider_value = gov.provider.value if hasattr(gov.provider, "value") else str(gov.provider)

    if provider_value == "agt":
        from swarmkit_runtime.governance.agt_provider import AGTGovernanceProvider  # noqa: PLC0415

        config = gov.config or {}
        policies_dir = ws_root / config.get("policies_dir", "policies")
        audit_db = ws_root / ".swarmkit" / "audit.db"
        audit_db.parent.mkdir(parents=True, exist_ok=True)
        return AGTGovernanceProvider.from_config(
            policy_dir=policies_dir,
            audit_db=audit_db,
        )

    if provider_value == "custom":
        print(
            "warning: governance.provider=custom is not yet supported; "
            "falling back to mock. See design §8.5 for the plugin path.",
            file=sys.stderr,
        )

    return MockGovernanceProvider(allow_all=True)


def resolve_authoring_provider(
    registry: ProviderRegistry | None = None,
) -> tuple[ModelProviderProtocol, str]:
    """Resolve which model provider + model name to use for authoring.

    Checks SWARMKIT_AUTHOR_MODEL (format: provider/model), then falls
    back to SWARMKIT_PROVIDER + SWARMKIT_MODEL, then first available
    real provider.
    """
    author_model = os.environ.get("SWARMKIT_AUTHOR_MODEL", "")
    if "/" in author_model:
        provider_id, model_name = author_model.split("/", 1)
    else:
        provider_id = os.environ.get("SWARMKIT_PROVIDER", "")
        model_name = os.environ.get("SWARMKIT_MODEL", "")

    if registry is None:
        registry = ProviderRegistry()
        register_available_providers(registry)

    if provider_id:
        provider = registry.get(provider_id)
        if provider is not None:
            return provider, model_name or "claude-sonnet-4-6"

    for pid in registry.provider_ids:
        if pid == "mock":
            continue
        provider = registry.get(pid)
        if provider is not None:
            return provider, model_name or "claude-sonnet-4-6"

    raise RuntimeError(
        "No model provider available. Set SWARMKIT_PROVIDER "
        "and the corresponding API key (e.g. GROQ_API_KEY)."
    )


def find_missing_mcp_servers(
    workspace: ResolvedWorkspace,
    mcp_configs: dict[str, MCPServerConfig],
) -> list[tuple[str, str]]:
    """Return ``(skill_id, server_id)`` pairs whose mcp_tool target is unconfigured."""
    missing: list[tuple[str, str]] = []
    for skill_id, skill in workspace.skills.items():
        impl = skill.raw.implementation
        impl_type = impl.get("type") if isinstance(impl, dict) else getattr(impl, "type", None)
        if impl_type != "mcp_tool":
            continue
        server_id = impl.get("server") if isinstance(impl, dict) else getattr(impl, "server", "")
        if server_id and server_id not in mcp_configs:
            missing.append((skill_id, server_id))
    return missing
