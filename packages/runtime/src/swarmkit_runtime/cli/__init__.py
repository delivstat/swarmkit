"""SwarmKit CLI — entry points for authoring and execution (design §14.2).

``swarmkit validate`` is the first real command (M1.6). The others are
stubs awaiting their milestones.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime.errors import ResolutionError, ResolutionErrors
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.resolver import ResolvedWorkspace, resolve_workspace

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


# ---- stubs for later milestones ----------------------------------------
#
# Milestone refs trace to `design/IMPLEMENTATION-PLAN.md`. Update the label
# if a subcommand's target milestone changes so the user-visible message
# stays honest.


@app.command()
def init() -> None:
    """Launch the Workspace Authoring Swarm in terminal chat mode (design §14.2)."""
    _not_implemented("init", milestone="M8 (Workspace Authoring Swarm)")


author_app = typer.Typer(help="Conversational authoring for topologies, skills, archetypes.")
app.add_typer(author_app, name="author")


@author_app.command("topology")
def author_topology(name: str | None = typer.Argument(None)) -> None:
    """Launch the Topology Authoring Swarm variant (design §14.2)."""
    _not_implemented("author topology", milestone="M7+ (authoring swarms)")


@author_app.command("skill")
def author_skill(name: str | None = typer.Argument(None)) -> None:
    """Launch the Skill Authoring Swarm (design §12)."""
    _not_implemented("author skill", milestone="M7 (Skill Authoring Swarm)")


@author_app.command("archetype")
def author_archetype(name: str | None = typer.Argument(None)) -> None:
    """Launch the Archetype Authoring Swarm variant (design §14.2)."""
    _not_implemented("author archetype", milestone="M7+ (authoring swarms)")


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

    # Resolve providers from workspace config.
    # For M3, use mock providers — real provider wiring lands when the
    # workspace schema gains a model_providers config block.
    model_provider = MockModelProvider()
    governance = MockGovernanceProvider()

    graph = compile_topology(
        topology,
        model_provider=model_provider,
        governance=governance,
    )

    user_input = input_text or ""
    if not user_input and not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
    if not user_input:
        user_input = "hello"

    result = asyncio.run(
        graph.ainvoke(
            {
                "input": user_input,
                "messages": [],
                "agent_results": {},
                "current_agent": "",
                "output": "",
            }
        )
    )

    output = result.get("output", "")
    if output:
        typer.echo(output)


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
