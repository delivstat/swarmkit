"""SwarmKit CLI — entry points for authoring and execution (design §14.2).

``swarmkit validate`` is the first real command (M1.6). The others are
stubs awaiting their milestones.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from swarmkit_runtime.authoring import run_authoring_session
from swarmkit_runtime.errors import ResolutionError, ResolutionErrors
from swarmkit_runtime.gaps import SkillGapLog
from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.mcp import MCPClientManager, MCPServerConfig, parse_mcp_servers
from swarmkit_runtime.model_providers import (
    AnthropicModelProvider,
    GoogleModelProvider,
    GroqModelProvider,
    MockModelProvider,
    OllamaModelProvider,
    OpenAIModelProvider,
    OpenRouterModelProvider,
    ProviderRegistry,
    TogetherModelProvider,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.resolver import ResolvedWorkspace, resolve_workspace
from swarmkit_runtime.review import FileReviewQueue

from ._knowledge import build_pack, find_repo_root
from ._render import render_errors, render_success, should_colour

app = typer.Typer(
    name="swarmkit",
    help="Compose, run, and grow multi-agent swarms.",
    no_args_is_help=True,
)


# ---- validate -----------------------------------------------------------

_EXIT_OK = 0
_EXIT_RESOLUTION_ERROR = 1
_EXIT_USAGE = 2
# Stubbed subcommands share the usage exit code — a command the user typed
# correctly but that isn't wired yet is, functionally, not-usable-as-typed.
_EXIT_NOT_IMPLEMENTED = _EXIT_USAGE


def _not_implemented(command: str, *, milestone: str) -> None:
    """Exit with a clean user-facing message for a stubbed subcommand.

    Replaces raw ``NotImplementedError`` — which leaks a Python traceback
    to stderr and makes the CLI feel broken. See
    ``design/details/cli-unimplemented-stubs.md``.
    """
    typer.echo(
        f"swarmkit {command}: not yet implemented — planned for {milestone}. "
        "See design/IMPLEMENTATION-PLAN.md for the roadmap.",
        err=True,
    )
    raise typer.Exit(_EXIT_NOT_IMPLEMENTED)


@app.command()
def validate(
    path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root (directory containing workspace.yaml).",
            show_default=False,
        ),
    ] = Path("."),
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit JSONL instead of human-formatted output.",
        ),
    ] = False,
    tree: Annotated[
        bool,
        typer.Option(
            "--tree",
            help="On success, print the fully-expanded resolved agent tree.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress the success summary; errors still print.",
        ),
    ] = False,
    color: Annotated[
        bool | None,
        typer.Option(
            "--color/--no-color",
            help=(
                "Override TTY auto-detection for coloured output. "
                "NO_COLOR env var always suppresses."
            ),
        ),
    ] = None,
) -> None:
    """Validate a SwarmKit workspace and print a resolved tree or errors."""
    use_colour = should_colour(sys.stdout.isatty(), color)
    workspace_root = path.resolve()

    try:
        workspace = resolve_workspace(workspace_root)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors),
            json_mode=json_output,
            workspace_root=workspace_root,
            color=use_colour,
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except FileNotFoundError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    if quiet:
        return

    _emit_success(
        workspace,
        json_mode=json_output,
        tree=tree,
        color=use_colour,
    )


def _emit_errors(
    errors: list[ResolutionError],
    *,
    json_mode: bool,
    workspace_root: Path,
    color: bool,
) -> None:
    if json_mode:
        for err in errors:
            typer.echo(json.dumps(_error_to_json(err)))
        n_files = len({err.artifact_path for err in errors})
        typer.echo(
            json.dumps(
                {
                    "event": "validate.summary",
                    "status": "failed",
                    "errors": len(errors),
                    "files_affected": n_files,
                }
            )
        )
    else:
        typer.echo(
            render_errors(errors, workspace_root=workspace_root, color=color),
            err=False,
        )


def _emit_success(
    workspace: ResolvedWorkspace,
    *,
    json_mode: bool,
    tree: bool,
    color: bool,
) -> None:
    if json_mode:
        typer.echo(
            json.dumps(
                {
                    "event": "validate.ok",
                    "workspace": _identifier(workspace.raw.metadata.id),
                    "topologies": len(workspace.topologies),
                    "skills": len(workspace.skills),
                    "archetypes": len(workspace.archetypes),
                    "triggers": len(workspace.triggers),
                }
            )
        )
        if tree:
            for topology_id in sorted(workspace.topologies.keys()):
                topology = workspace.topologies[topology_id]
                typer.echo(
                    json.dumps(
                        {
                            "event": "validate.topology",
                            "id": topology_id,
                            "root": _agent_to_json(topology.root),
                        }
                    )
                )
        return

    typer.echo(render_success(workspace, tree=tree, color=color))


def _error_to_json(err: ResolutionError) -> dict[str, object]:
    data = asdict(err)
    # Paths aren't JSON-serialisable by default.
    data["artifact_path"] = str(err.artifact_path)
    data["related"] = [_error_to_json(r) for r in err.related]
    data["event"] = "validate.error"
    return data


def _agent_to_json(agent: object) -> dict[str, object]:
    return {
        "id": getattr(agent, "id", None),
        "role": getattr(agent, "role", None),
        "archetype": getattr(agent, "source_archetype", None),
        "model": dict(getattr(agent, "model", None) or {}),
        "skills": [s.id for s in getattr(agent, "skills", ())],
        "children": [_agent_to_json(c) for c in getattr(agent, "children", ())],
    }


def _identifier(value: object) -> str:
    root = getattr(value, "root", value)
    return str(root)


def _stderr(msg: str) -> None:
    typer.echo(msg, err=True)


# ---- knowledge-pack -----------------------------------------------------


@app.command(name="knowledge-pack")
def knowledge_pack(
    workspace: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Optional workspace directory. If given, the workspace YAML "
                "and the output of `swarmkit validate` against it are "
                "appended to the pack."
            ),
            show_default=False,
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the pack to FILE instead of stdout.",
            show_default=False,
        ),
    ] = None,
    include_fixtures: Annotated[
        bool,
        typer.Option(
            "--fixtures/--no-fixtures",
            help="Include schema fixtures (valid + invalid examples).",
        ),
    ] = True,
) -> None:
    """Bundle SwarmKit docs + schemas + workspace state into a paste-ready prompt."""
    repo_root = find_repo_root()
    if repo_root is None:
        _stderr(
            "swarmkit knowledge-pack: could not locate the SwarmKit repo on disk. "
            "This command currently requires a source checkout (the corpus is not "
            "yet bundled as package data — see design/details/knowledge-pack-cli.md)."
        )
        raise typer.Exit(_EXIT_USAGE)

    if workspace is not None and not workspace.exists():
        _stderr(f"swarmkit knowledge-pack: workspace path not found: {workspace}")
        raise typer.Exit(_EXIT_USAGE)

    pack = build_pack(
        repo_root,
        workspace=workspace.resolve() if workspace else None,
        include_fixtures=include_fixtures,
    )

    if output is not None:
        output.write_text(pack, encoding="utf-8")
        return
    typer.echo(pack, nl=False)


# ---- review + gaps -------------------------------------------------------

review_app = typer.Typer(help="Human-in-the-loop review queue.")
app.add_typer(review_app, name="review")


@review_app.command("list")
def review_list(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """List pending review items."""

    queue = FileReviewQueue(workspace_path.resolve())
    pending = queue.list_pending()
    if not pending:
        typer.echo("No pending reviews.")
        return
    for item in pending:
        typer.echo(f"  {item.id[:8]}  {item.agent_id:<16} {item.skill_id:<24} {item.reason}")


@review_app.command("show")
def review_show(
    item_id: str,
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Show full details of a review item."""

    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            typer.echo(f"ID:       {item.id}")
            typer.echo(f"Agent:    {item.agent_id}")
            typer.echo(f"Skill:    {item.skill_id}")
            typer.echo(f"Status:   {item.status}")
            typer.echo(f"Reason:   {item.reason}")
            typer.echo(f"Output:   {json.dumps(item.output, indent=2)}")
            typer.echo(f"Verdict:  {json.dumps(item.verdict, indent=2)}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("approve")
def review_approve(
    item_id: str,
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Approve a pending review item."""

    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            queue.resolve(item.id, "approved")
            typer.echo(f"✓ Approved {item.id[:8]}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("reject")
def review_reject(
    item_id: str,
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Reject a pending review item."""

    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            queue.resolve(item.id, "rejected")
            typer.echo(f"✗ Rejected {item.id[:8]}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@app.command()
def gaps(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """List recorded skill gaps."""

    log = SkillGapLog(workspace_path.resolve())
    gap_list = log.list_gaps()
    if not gap_list:
        typer.echo("No skill gaps recorded.")
        return
    for gap in gap_list:
        typer.echo(
            f"  {gap.skill_id:<24} {gap.pattern:<40} ({gap.occurrences}x) → {gap.suggested_action}"
        )


# ---- stubs for later milestones ----------------------------------------
#
# Milestone refs trace to `design/IMPLEMENTATION-PLAN.md`. Update the label
# if a subcommand's target milestone changes so the user-visible message
# stays honest.


@app.command()
def init(
    path: Annotated[
        Path,
        typer.Argument(help="Directory to create the workspace in.", show_default=False),
    ] = Path("."),
) -> None:
    """Create a new SwarmKit workspace through conversation."""
    provider, model = _resolve_authoring_provider()
    run_authoring_session(
        mode="init",
        model_provider=provider,
        model_name=model,
        workspace_path=path.resolve(),
    )


author_app = typer.Typer(help="Conversational authoring for topologies, skills, archetypes.")
app.add_typer(author_app, name="author")


@author_app.command("topology")
def author_topology(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace directory.", show_default=False),
    ] = Path("."),
) -> None:
    """Author a new topology through conversation."""
    provider, model = _resolve_authoring_provider()
    run_authoring_session(
        mode="topology",
        model_provider=provider,
        model_name=model,
        workspace_path=workspace_path.resolve(),
    )


@author_app.command("skill")
def author_skill(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace directory.", show_default=False),
    ] = Path("."),
) -> None:
    """Author a new skill through conversation."""
    provider, model = _resolve_authoring_provider()
    run_authoring_session(
        mode="skill",
        model_provider=provider,
        model_name=model,
        workspace_path=workspace_path.resolve(),
    )


@author_app.command("archetype")
def author_archetype(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace directory.", show_default=False),
    ] = Path("."),
) -> None:
    """Author a new archetype through conversation."""
    provider, model = _resolve_authoring_provider()
    run_authoring_session(
        mode="archetype",
        model_provider=provider,
        model_name=model,
        workspace_path=workspace_path.resolve(),
    )


@author_app.command("mcp-server")
def author_mcp_server(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace directory.", show_default=False),
    ] = Path("."),
) -> None:
    """Author a new MCP server through conversation."""
    provider, model = _resolve_authoring_provider()
    run_authoring_session(
        mode="mcp-server",
        model_provider=provider,
        model_name=model,
        workspace_path=workspace_path.resolve(),
    )


def _resolve_authoring_provider() -> tuple[ModelProviderProtocol, str]:
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

    registry = ProviderRegistry()
    _register_available_providers(registry)

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

    _stderr(
        "No model provider available for authoring. Set SWARMKIT_PROVIDER "
        "and the corresponding API key (e.g. GROQ_API_KEY)."
    )
    raise typer.Exit(_EXIT_USAGE)


@app.command()
def run(
    workspace_path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root directory (containing workspace.yaml).",
            show_default=False,
        ),
    ],
    topology_name: Annotated[
        str,
        typer.Argument(help="Name of the topology to run."),
    ],
    input_text: Annotated[
        str | None,
        typer.Option(
            "--input",
            "-i",
            help="User input to send to the swarm. Reads from stdin if omitted.",
        ),
    ] = None,
    color: Annotated[
        bool | None,
        typer.Option("--color/--no-color"),
    ] = None,
) -> None:
    """One-shot execution of a topology (design §14.1)."""
    use_colour = should_colour(sys.stdout.isatty(), color)
    ws_root = workspace_path.resolve()

    try:
        workspace = resolve_workspace(ws_root)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors),
            json_mode=False,
            workspace_root=ws_root,
            color=use_colour,
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if topology_name not in workspace.topologies:
        available = sorted(workspace.topologies.keys())
        _stderr(
            f"Topology '{topology_name}' not found in workspace. "
            f"Available: {', '.join(available) or '(none)'}."
        )
        raise typer.Exit(_EXIT_USAGE)

    topology = workspace.topologies[topology_name]

    registry = ProviderRegistry()
    _register_available_providers(registry)
    governance = _build_governance(workspace, ws_root)

    mcp_configs = parse_mcp_servers(getattr(workspace.raw, "mcp_servers", None))
    mcp_manager = MCPClientManager(mcp_configs, workspace_root=ws_root) if mcp_configs else None

    missing_servers = _missing_mcp_servers(workspace, mcp_configs)
    if missing_servers:
        for skill_id, server_id in missing_servers:
            _stderr(
                f"error: skill '{skill_id}' targets MCP server '{server_id}' "
                f"but the workspace declares no such server. "
                f"Add it under `mcp_servers:` in workspace.yaml, "
                f"or change the skill's `implementation.server` to a configured server."
            )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR)

    graph = compile_topology(
        topology,
        provider_registry=registry,
        governance=governance,
        mcp_manager=mcp_manager,
    )

    user_input = input_text or ""
    if not user_input and not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
    if not user_input:
        user_input = "hello"

    _MAX_GRAPH_STEPS = 10

    async def _invoke() -> dict[str, Any]:
        if mcp_manager is not None:
            await mcp_manager.start_all()
        try:
            return await graph.ainvoke(
                {
                    "input": user_input,
                    "messages": [],
                    "agent_results": {},
                    "current_agent": "",
                    "output": "",
                },
                config={"recursion_limit": _MAX_GRAPH_STEPS},
            )
        finally:
            # Close MCP sessions inside the same task that opened them, so
            # the SDK's anyio task groups unwind cleanly. Skipping this
            # leaves the cleanup to interpreter shutdown, which the SDK
            # logs as a noisy "cancel scope in a different task" trace.
            if mcp_manager is not None:
                await mcp_manager.close_all()

    try:
        result = asyncio.run(_invoke())
    except Exception as exc:
        _stderr(f"error: execution failed: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    output = result.get("output", "")
    if output:
        typer.echo(output)


def _missing_mcp_servers(
    workspace: ResolvedWorkspace,
    mcp_configs: dict[str, MCPServerConfig],
) -> list[tuple[str, str]]:
    """Return ``(skill_id, server_id)`` pairs whose mcp_tool target is unconfigured.

    Catches the "skill points at hello-world but workspace declares no
    such server" mistake at compile time, so the user sees a single clear
    error instead of a cryptic runtime message buried in the topology output.
    """
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


def _register_available_providers(registry: ProviderRegistry) -> None:
    """Register all providers that have credentials available."""
    registry.register(MockModelProvider())

    if os.environ.get("ANTHROPIC_API_KEY"):
        registry.register(AnthropicModelProvider())
    if os.environ.get("GOOGLE_API_KEY"):
        registry.register(GoogleModelProvider())
    if os.environ.get("OPENAI_API_KEY"):
        registry.register(OpenAIModelProvider())
    if os.environ.get("OPENROUTER_API_KEY"):
        registry.register(OpenRouterModelProvider())
    if os.environ.get("GROQ_API_KEY"):
        registry.register(GroqModelProvider())
    if os.environ.get("TOGETHER_API_KEY"):
        registry.register(TogetherModelProvider())

    registry.register(OllamaModelProvider())


def _build_governance(workspace: ResolvedWorkspace, ws_root: Path) -> GovernanceProvider:
    """Select the GovernanceProvider based on workspace.yaml's governance block.

    - ``provider: agt`` → ``AGTGovernanceProvider.from_config()``, reading
      ``config.policies_dir`` relative to the workspace root.
    - ``provider: mock`` or absent → ``MockGovernanceProvider()``.
    - ``provider: custom`` → falls back to Mock with a warning (custom
      plugin path not yet implemented).
    """
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
        _stderr(
            "warning: governance.provider=custom is not yet supported; "
            "falling back to mock. See design §8.5 for the plugin path."
        )

    return MockGovernanceProvider(allow_all=True)


@app.command()
def serve(path: str, port: int = 8000) -> None:
    """Persistent / scheduled mode (design §14.1)."""
    _not_implemented("serve", milestone="M9 (HTTP server + scheduled mode)")


@app.command()
def eject(topology: str, output: str = "./generated/") -> None:
    """Export the LangGraph code the runtime would execute (design §14.4)."""
    _not_implemented("eject", milestone="M9 (eject)")


if __name__ == "__main__":
    app()
