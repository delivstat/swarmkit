"""CLI commands — validate, gaps, init, the review queue, conversational authoring, edit."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    pass

import typer

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
    resolve_authoring_provider,
)
from swarmkit_runtime.authoring import run_authoring_session
from swarmkit_runtime.authoring._prompts import AuthoringMode
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.gaps import SkillGapLog
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue

from ._app import app, author_app, review_app
from ._common import (
    _EXIT_RESOLUTION_ERROR,
    _EXIT_USAGE,
    _emit_errors,
    _emit_success,
    _print_banner,
    _stderr,
    _suppress_noisy_logs,
)
from ._render import should_colour

# ---- validate -----------------------------------------------------------


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
        typer.Option("--json", help="Emit JSONL instead of human-formatted output."),
    ] = False,
    tree: Annotated[
        bool,
        typer.Option("--tree", help="On success, print the fully-expanded resolved agent tree."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress the success summary; errors still print."),
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
            list(exc.errors), json_mode=json_output, workspace_root=workspace_root, color=use_colour
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except FileNotFoundError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    if quiet:
        return

    _emit_success(workspace, json_mode=json_output, tree=tree, color=use_colour)


# ---- review + gaps -------------------------------------------------------


@review_app.command("list")
def review_list(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
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
        Path, typer.Argument(help="Workspace root.", show_default=False)
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
            # Friendly rendering for harness gates (§6.2 permission / §6.3 input).
            if item.skill_id == "harness-approval":
                typer.echo(f"Capability: {item.output.get('capability', '')}")
                typer.echo("Resolve with: swarmkit review approve|reject <id>")
            elif item.skill_id == "harness-input":
                typer.echo(f"Question:   {item.output.get('question', '')}")
                options = item.output.get("options") or []
                for i, opt in enumerate(options):
                    typer.echo(f"  [{i}] {opt}")
                if item.output.get("free_text_allowed", True):
                    typer.echo("  (free text also accepted)")
                typer.echo('Resolve with: swarmkit review answer <id> "<your answer>"')
            else:
                typer.echo(f"Output:   {json.dumps(item.output, indent=2)}")
                typer.echo(f"Verdict:  {json.dumps(item.verdict, indent=2)}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("approve")
def review_approve(
    item_id: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
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
        Path, typer.Argument(help="Workspace root.", show_default=False)
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


@review_app.command("answer")
def review_answer(
    item_id: str,
    answer: Annotated[str, typer.Argument(help="The answer text (or an option shown by `show`).")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Answer a harness input request (§6.3) with text. Inspect it first with `review show <id>`.

    An answer that is a bare integer selects that option index from the request; otherwise the text
    is used verbatim (when the request allows free text)."""
    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if not item.id.startswith(item_id):
            continue
        resolved = answer
        options = item.output.get("options") or []
        if answer.isdigit() and 0 <= int(answer) < len(options):
            resolved = str(options[int(answer)])
        queue.answer_input(item.id, resolved)
        typer.echo(f"✓ Answered {item.id[:8]}: {resolved!r}")
        return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@app.command()
def gaps(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
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


# ---- authoring -----------------------------------------------------------


@app.command()
def init(
    path: Annotated[
        Path, typer.Argument(help="Directory to create the workspace in.", show_default=False)
    ] = Path("."),
) -> None:
    """Create a new SwarmKit workspace through conversation."""
    _print_banner()
    _suppress_noisy_logs()
    provider, model = resolve_authoring_provider()
    run_authoring_session(
        mode="init", model_provider=provider, model_name=model, workspace_path=path.resolve()
    )


def _run_authoring(
    mode: AuthoringMode,
    workspace_path: Path,
    thorough: bool,
    input_text: str = "",
) -> None:
    """Route authoring to single-agent (quick) or swarm (thorough)."""
    _print_banner()
    _suppress_noisy_logs()
    if thorough:
        try:
            runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
            prompt = f"Create a new {mode}. {input_text}".strip()
            result = asyncio.run(runtime.run("skill-authoring", prompt))
            if result.output:
                typer.echo(result.output)
        except KeyError:
            _stderr(
                "error: --thorough requires the skill-authoring topology in the workspace. "
                "Add it from reference/topologies/skill-authoring.yaml."
            )
            raise typer.Exit(_EXIT_USAGE) from None
        except Exception as exc:
            _stderr(f"error: authoring failed: {exc}")
            raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    else:
        provider, model = resolve_authoring_provider()
        run_authoring_session(
            mode=mode,
            model_provider=provider,
            model_name=model,
            workspace_path=workspace_path.resolve(),
        )


@author_app.command("topology")
def author_topology(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new topology through conversation."""
    _run_authoring("topology", workspace_path, thorough)


@author_app.command("skill")
def author_skill(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new skill through conversation."""
    _run_authoring("skill", workspace_path, thorough)


@author_app.command("archetype")
def author_archetype(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new archetype through conversation."""
    _run_authoring("archetype", workspace_path, thorough)


@author_app.command("mcp-server")
def author_mcp_server(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new MCP server through conversation."""
    _run_authoring("mcp-server", workspace_path, thorough)


# ---- edit (M7 — Skill Authoring Swarm in edit mode) ----------------------


@app.command()
def edit(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace to edit.", show_default=False),
    ] = Path("."),
    input_text: Annotated[
        str | None,
        typer.Option(
            "--input",
            "-i",
            help="Describe the change (or omit for interactive conversation).",
        ),
    ] = None,
    color: Annotated[bool | None, typer.Option("--color/--no-color")] = None,
) -> None:
    """Edit an existing workspace through conversation (M7 Skill Authoring Swarm).

    Reads the current workspace state, understands the requested change,
    drafts modifications, validates, and writes. The user never edits
    YAML directly.
    """
    use_colour = should_colour(sys.stdout.isatty(), color)

    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors),
            json_mode=False,
            workspace_root=workspace_path.resolve(),
            color=use_colour,
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except MissingMCPServerError as exc:
        for skill_id, server_id in exc.missing:
            _stderr(
                f"error: skill '{skill_id}' targets MCP server '{server_id}' "
                f"but the workspace declares no such server."
            )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    user_input = input_text or ""
    if not user_input and not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
    if not user_input:
        user_input = "What would you like to change in this workspace?"

    try:
        result = asyncio.run(runtime.run("skill-authoring", user_input))
    except KeyError:
        _stderr(
            "error: the skill-authoring topology is not available in this workspace. "
            "Add it from reference/topologies/skill-authoring.yaml, or use "
            "`swarmkit author` for single-agent authoring."
        )
        raise typer.Exit(_EXIT_USAGE) from None
    except Exception as exc:
        _stderr(f"error: edit failed: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if result.output:
        typer.echo(result.output)
