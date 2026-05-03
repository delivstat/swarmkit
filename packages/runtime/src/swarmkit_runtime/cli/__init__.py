"""SwarmKit CLI — thin interface over WorkspaceRuntime (design §14.2).

Argument parsing and output rendering only. Business logic lives in
``WorkspaceRuntime`` (``_workspace_runtime.py``).
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

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    RunResult,
    WorkspaceRuntime,
    resolve_authoring_provider,
)
from swarmkit_runtime.authoring import run_authoring_session
from swarmkit_runtime.authoring._prompts import AuthoringMode
from swarmkit_runtime.errors import ResolutionError, ResolutionErrors
from swarmkit_runtime.gaps import SkillGapLog
from swarmkit_runtime.resolver import ResolvedWorkspace, resolve_workspace
from swarmkit_runtime.review import FileReviewQueue

from ._knowledge import build_pack, find_repo_root
from ._render import render_errors, render_success, should_colour

app = typer.Typer(
    name="swarmkit",
    help="Compose, run, and grow multi-agent swarms.",
    no_args_is_help=True,
)


# ---- exit codes -----------------------------------------------------------

_EXIT_OK = 0
_EXIT_RESOLUTION_ERROR = 1
_EXIT_USAGE = 2
_EXIT_NOT_IMPLEMENTED = _EXIT_USAGE


def _not_implemented(command: str, *, milestone: str) -> None:
    typer.echo(
        f"swarmkit {command}: not yet implemented — planned for {milestone}. "
        "See design/IMPLEMENTATION-PLAN.md for the roadmap.",
        err=True,
    )
    raise typer.Exit(_EXIT_NOT_IMPLEMENTED)


def _stderr(msg: str) -> None:
    typer.echo(msg, err=True)


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
        typer.echo(render_errors(errors, workspace_root=workspace_root, color=color), err=False)


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


# ---- knowledge-pack -----------------------------------------------------


@app.command(name="knowledge-pack")
def knowledge_pack(
    workspace: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Optional workspace directory. If given, workspace YAML "
                "and validation are appended."
            ),
            show_default=False,
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Write the pack to FILE instead of stdout.", show_default=False
        ),
    ] = None,
    include_fixtures: Annotated[
        bool,
        typer.Option(
            "--fixtures/--no-fixtures", help="Include schema fixtures (valid + invalid examples)."
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
    provider, model = resolve_authoring_provider()
    run_authoring_session(
        mode="init", model_provider=provider, model_name=model, workspace_path=path.resolve()
    )


author_app = typer.Typer(help="Conversational authoring for topologies, skills, archetypes.")
app.add_typer(author_app, name="author")


def _run_authoring(
    mode: AuthoringMode,
    workspace_path: Path,
    thorough: bool,
    input_text: str = "",
) -> None:
    """Route authoring to single-agent (quick) or swarm (thorough)."""
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


# ---- run -----------------------------------------------------------------


@app.command()
def run(
    workspace_path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root directory (containing workspace.yaml).",
            show_default=False,
        ),
    ],
    topology_name: Annotated[str, typer.Argument(help="Name of the topology to run.")],
    input_text: Annotated[
        str | None,
        typer.Option(
            "--input", "-i", help="User input to send to the swarm. Reads from stdin if omitted."
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print per-agent execution summary after output."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show resolved agents and skills without executing.",
        ),
    ] = False,
    color: Annotated[bool | None, typer.Option("--color/--no-color")] = None,
) -> None:
    """One-shot execution of a topology (design §14.1)."""
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
                f"but the workspace declares no such server. "
                f"Add it under `mcp_servers:` in workspace.yaml, "
                f"or change the skill's `implementation.server` to a configured server."
            )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if dry_run:
        _print_dry_run(runtime, topology_name)
        return

    if verbose:
        os.environ["SWARMKIT_VERBOSE"] = "1"

    user_input = input_text or ""
    if not user_input and not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
    if not user_input:
        user_input = "hello"

    try:
        result = asyncio.run(runtime.run(topology_name, user_input))
    except KeyError as exc:
        _stderr(str(exc).strip("'\""))
        raise typer.Exit(_EXIT_USAGE) from exc
    except Exception as exc:
        _stderr(f"error: execution failed: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if result.output:
        typer.echo(result.output)

    _save_run_log(workspace_path.resolve(), topology_name, result)

    if verbose and result.events:
        _print_run_summary(result)


# ---- chat (multi-turn conversation) --------------------------------------


@app.command()
def chat(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ],
    topology_name: Annotated[str, typer.Argument(help="Topology to converse with.")],
    resume_id: Annotated[
        str | None,
        typer.Option("--resume", "-r", help="Resume a previous conversation by ID."),
    ] = None,
) -> None:
    """Interactive multi-turn conversation with a topology.

    Each turn runs the topology with accumulated conversation history.
    The swarm sees the full context of what was discussed before.
    Conversations are saved to .swarmkit/conversations/ and can be
    resumed with --resume <id>.
    """
    from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except (ResolutionErrors, MissingMCPServerError) as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    manager = ConversationManager(runtime, workspace_path.resolve())

    if resume_id:
        conv = manager.resume(resume_id)
        if conv is None:
            _stderr(f"Conversation '{resume_id}' not found.")
            raise typer.Exit(_EXIT_USAGE)
        _show_and_continue_conversation(conv, manager)
    else:
        conv = manager.create(topology_name)
        typer.echo(f"New conversation {conv.id} with topology '{topology_name}'")
        typer.echo("Type your message. Ctrl+C to exit.\n")
        _conversation_loop(conv, manager)


@app.command(name="conversations")
def list_conversations(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    last: Annotated[int, typer.Option("--last", "-n")] = 10,
    pick: Annotated[
        bool,
        typer.Option("--pick", "-p", help="Select a conversation to resume."),
    ] = False,
) -> None:
    """List saved conversations. Use --pick to resume one interactively."""
    from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except Exception:
        _stderr("Could not load workspace.")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from None

    manager = ConversationManager(runtime, workspace_path.resolve())
    convos = manager.list_conversations(last=last)

    if not convos:
        typer.echo("No conversations yet. Start one with: swarmkit chat <workspace> <topology>")
        return

    typer.echo("\nRecent conversations:\n")
    for i, c in enumerate(convos, 1):
        last_msg = c.get("last_message", "")
        typer.echo(f"  {i}. [{c['id']}] {c['topology']} ({c['turns']} turns)")
        if last_msg:
            typer.echo(f'     "{last_msg}"')
        typer.echo(f"     {c['updated']}")
        typer.echo()

    if not pick:
        typer.echo("Resume with: swarmkit chat <workspace> <topology> --resume <id>")
        typer.echo("Or use: swarmkit conversations <workspace> --pick")
        return

    try:
        choice = input(f"Pick a conversation (1-{len(convos)}): ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    try:
        idx = int(choice) - 1
        if not 0 <= idx < len(convos):
            raise ValueError
    except ValueError:
        _stderr(f"Invalid choice: {choice}")
        raise typer.Exit(_EXIT_USAGE) from None

    selected = convos[idx]
    conv = manager.resume(selected["id"])
    if conv is None:
        _stderr("Could not load conversation.")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from None

    _show_and_continue_conversation(conv, manager)


def _show_and_continue_conversation(conv: Any, manager: Any) -> None:
    """Print conversation history and start the interactive loop."""
    typer.echo(f"\nResumed: {conv.id} ({len(conv.turns)} turns)\n")
    for turn in conv.turns:
        prefix = "You" if turn.role == "human" else "Swarm"
        typer.echo(f"  {prefix}: {turn.content[:100]}{'...' if len(turn.content) > 100 else ''}")
    typer.echo()
    _conversation_loop(conv, manager)


_EXIT_COMMANDS = {"exit", "quit", "bye", "/exit", "/quit"}


def _build_chat_session() -> Any:
    """Build a prompt_toolkit session with history and completion."""
    from prompt_toolkit import PromptSession  # noqa: PLC0415
    from prompt_toolkit.completion import WordCompleter  # noqa: PLC0415
    from prompt_toolkit.history import FileHistory  # noqa: PLC0415

    history_path = Path.home() / ".swarmkit" / "chat_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    commands = WordCompleter(
        [
            "/model",
            "/model reset",
            "exit",
            "quit",
            "bye",
            "/exit",
            "/quit",
        ],
        sentence=True,
    )

    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=commands,
        enable_history_search=True,
    )


def _conversation_loop(conv: Any, manager: Any) -> None:
    """Interactive REPL for a conversation.

    Starts MCP servers once and keeps them alive across turns.
    """
    asyncio.run(_async_conversation_loop(conv, manager))


async def _async_conversation_loop(conv: Any, manager: Any) -> None:
    session = _build_chat_session()
    await manager.start_session()
    try:
        while True:
            try:
                user_input = (await session.prompt_async("> ")).strip()
            except (KeyboardInterrupt, EOFError):
                user_input = "exit"

            if not user_input:
                continue
            if user_input.lower() in _EXIT_COMMANDS:
                typer.echo(f"\nConversation saved: {conv.id}")
                typer.echo(f"Resume with: swarmkit chat ... --resume {conv.id}")
                break

            # /model command — switch model dynamically
            if user_input.startswith("/model"):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2:
                    current = os.environ.get("SWARMKIT_MODEL", "(topology default)")
                    provider = os.environ.get("SWARMKIT_PROVIDER", "(topology default)")
                    typer.echo(f"Current model: {current} (provider: {provider})")
                    typer.echo("Usage: /model <provider/model> or /model <model>")
                    typer.echo("Example: /model deepseek/deepseek-chat")
                    typer.echo("         /model qwen/qwen3-235b-a22b")
                    typer.echo("         /model reset  (back to topology defaults)")
                else:
                    model_spec = parts[1].strip()
                    if model_spec == "reset":
                        os.environ.pop("SWARMKIT_MODEL", None)
                        os.environ.pop("SWARMKIT_PROVIDER", None)
                        typer.echo("Model reset to topology defaults.")
                    elif "/" in model_spec:
                        os.environ["SWARMKIT_PROVIDER"] = "openrouter"
                        os.environ["SWARMKIT_MODEL"] = model_spec
                        typer.echo(f"Switched to: {model_spec} (via openrouter)")
                    else:
                        os.environ["SWARMKIT_MODEL"] = model_spec
                        typer.echo(f"Switched model to: {model_spec}")
                continue

            try:
                result = await manager.send(conv, user_input)
            except Exception as exc:
                _stderr(f"error: {exc}")
                continue
            typer.echo(f"\n{result.output}\n")
    finally:
        await manager.end_session()


# ---- dry run -------------------------------------------------------------


def _print_dry_run(runtime: WorkspaceRuntime, topology_name: str) -> None:
    """Show the resolved topology without executing — no LLM or MCP calls."""
    ws = runtime.workspace
    if topology_name not in ws.topologies:
        available = sorted(ws.topologies.keys())
        _stderr(f"Topology '{topology_name}' not found. Available: {available}")
        raise typer.Exit(_EXIT_USAGE)

    topology = ws.topologies[topology_name]
    typer.echo(f"── dry run: {topology_name} ──\n")
    typer.echo("Agents:")
    _print_agent_tree(topology.root, indent=2)

    mcp_ids = runtime.mcp_manager.server_ids if runtime.mcp_manager else []
    if mcp_ids:
        typer.echo(f"\nMCP servers: {', '.join(mcp_ids)}")

    gov_type = type(runtime.governance).__name__
    typer.echo(f"Governance: {gov_type}")
    typer.echo("\nNo LLM or MCP calls made. Use without --dry-run to execute.")


def _print_agent_tree(agent: object, indent: int = 0) -> None:
    prefix = " " * indent
    agent_id = getattr(agent, "id", "?")
    role = getattr(agent, "role", "?")
    model = getattr(agent, "model", None) or {}
    provider = model.get("provider", "?") if isinstance(model, dict) else "?"
    model_name = model.get("name", "?") if isinstance(model, dict) else "?"
    skills = [s.id for s in getattr(agent, "skills", ())]

    typer.echo(f"{prefix}{agent_id} ({role}) — {provider}/{model_name}")
    if skills:
        typer.echo(f"{prefix}  skills: {', '.join(skills)}")
    for child in getattr(agent, "children", ()):
        _print_agent_tree(child, indent + 4)


# ---- run observability helpers -------------------------------------------


def _print_run_summary(result: RunResult) -> None:
    """Print a per-agent execution summary from run events."""
    typer.echo("\n── run summary ──")
    completed = [e for e in result.events if e.event_type == "agent.completed"]
    denied = [e for e in result.events if e.event_type in ("policy.denied", "trust.denied")]
    skills = [e for e in result.events if e.event_type == "skill.executed"]
    validation_fails = [e for e in result.events if e.event_type == "output.validation_failed"]

    for evt in completed:
        duration = evt.payload.get("duration_ms", "?")
        role = evt.payload.get("role", "")
        typer.echo(f"  {evt.agent_id:<24} {role:<8} {duration:>6}ms")

    if skills:
        typer.echo(f"\n  skills called: {len(skills)}")
    if denied:
        typer.echo(f"  policy denials: {len(denied)}")
        for d in denied:
            typer.echo(f"    {d.agent_id}: {d.payload.get('reason', '')}")
    if validation_fails:
        typer.echo(f"  output validation failures: {len(validation_fails)}")
        for v in validation_fails:
            typer.echo(f"    {v.agent_id}: {v.payload.get('error', '')}")

    typer.echo(f"  total events: {len(result.events)}")


def _save_run_log(ws_root: Path, topology: str, result: RunResult) -> None:
    """Save run events to .swarmkit/logs/ as JSONL for later analysis."""
    if not result.events:
        return
    from datetime import UTC, datetime  # noqa: PLC0415

    log_dir = ws_root / ".swarmkit" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"{topology}-{ts}.jsonl"
    lines = []
    for evt in result.events:
        entry = {
            "event_type": evt.event_type,
            "agent_id": evt.agent_id,
            "timestamp": evt.timestamp,
            "skill_id": evt.skill_id,
            **evt.payload,
        }
        lines.append(json.dumps(entry))
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---- logs ----------------------------------------------------------------


@app.command()
def logs(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Show last N runs."),
    ] = 1,
    topology: Annotated[
        str | None,
        typer.Option("--topology", "-t", help="Filter by topology name."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text (default) or markdown."),
    ] = "text",
) -> None:
    """Show events from recent topology runs.

    Reads from .swarmkit/logs/*.jsonl saved by swarmkit run.
    Use --format markdown for a compliance-ready audit report.
    """
    log_dir = workspace_path.resolve() / ".swarmkit" / "logs"
    if not log_dir.is_dir():
        typer.echo("No run logs found. Run a topology with `swarmkit run` first.")
        return

    log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)
    if topology:
        log_files = [f for f in log_files if f.name.startswith(f"{topology}-")]
    log_files = log_files[:last]

    if not log_files:
        typer.echo("No matching run logs found.")
        return

    for log_file in reversed(log_files):
        events = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").strip().split("\n")
            if line
        ]
        if format == "markdown":
            typer.echo(_format_log_markdown(log_file.name, events))
        else:
            typer.echo(f"\n── {log_file.name} ──")
            for evt in events:
                typer.echo(_format_log_event(evt))


def _format_log_markdown(filename: str, events: list[dict[str, object]]) -> str:
    """Format a run log as a compliance-ready markdown audit report."""
    completed = [e for e in events if e.get("event_type") == "agent.completed"]
    denied = [e for e in events if "denied" in str(e.get("event_type", "")).lower()]
    fails = [e for e in events if "failed" in str(e.get("event_type", "")).lower()]
    skills = [e for e in events if e.get("event_type") == "skill.executed"]

    lines = [
        f"# Run Report: {filename}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Agents completed | {len(completed)} |",
        f"| Skills called | {len(skills)} |",
        f"| Policy denials | {len(denied)} |",
        f"| Validation failures | {len(fails)} |",
        f"| Total events | {len(events)} |",
        "",
    ]

    if completed:
        lines.extend(["## Agent Performance", "", "| Agent | Role | Duration |", "|---|---|---|"])
        for e in completed:
            lines.append(
                f"| {e.get('agent_id', '')} | {e.get('role', '')} | {e.get('duration_ms', '?')}ms |"
            )
        lines.append("")

    if denied:
        lines.extend(["## Policy Denials", ""])
        for e in denied:
            lines.append(f"- **{e.get('agent_id', '')}**: {e.get('reason', '')}")
        lines.append("")

    if fails:
        lines.extend(["## Validation Failures", ""])
        for e in fails:
            lines.append(f"- **{e.get('agent_id', '')}**: {e.get('error', '')}")
        lines.append("")

    lines.extend(["## Event Timeline", "", "| Timestamp | Agent | Event |", "|---|---|---|"])
    for e in events:
        ts = str(e.get("timestamp", ""))[:19]
        lines.append(f"| {ts} | {e.get('agent_id', '')} | {e.get('event_type', '')} |")

    return "\n".join(lines)


def _format_log_event(evt: dict[str, object]) -> str:
    agent = str(evt.get("agent_id", ""))
    etype = str(evt.get("event_type", ""))
    detail = {
        "agent.started": f"started  ({evt.get('role', '')})",
        "agent.completed": f"done     {evt.get('duration_ms', '?')}ms",
        "skill.executed": f"skill    {evt.get('skill_id', '')}",
        "policy.denied": f"DENIED   {evt.get('reason', '')}",
        "trust.denied": f"DENIED   {evt.get('reason', '')}",
        "output.validation_failed": f"FAIL     {evt.get('error', '')}",
        "output.validated": "valid",
    }.get(etype, etype)
    return f"  {agent:<24} {detail}"


# ---- status --------------------------------------------------------------


@app.command()
def status(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Show last N runs."),
    ] = 5,
) -> None:
    """Show recent run status at a glance."""
    log_dir = workspace_path.resolve() / ".swarmkit" / "logs"
    if not log_dir.is_dir():
        typer.echo("No runs recorded yet.")
        return

    log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:last]
    if not log_files:
        typer.echo("No runs recorded yet.")
        return

    typer.echo(f"{'topology':<20} {'agents':<8} {'duration':<10} {'issues':<8} {'when'}")
    typer.echo("-" * 65)
    for lf in log_files:
        events = [json.loads(line) for line in lf.read_text().strip().split("\n") if line]
        topo = lf.stem.rsplit("-", 1)[0]
        completed = [e for e in events if e.get("event_type") == "agent.completed"]
        denied = [e for e in events if "denied" in str(e.get("event_type", "")).lower()]
        fails = [e for e in events if "failed" in str(e.get("event_type", "")).lower()]
        total_ms = sum(int(e.get("duration_ms", 0)) for e in completed)
        issues = len(denied) + len(fails)
        ts = lf.stem.rsplit("-", 1)[-1] if "-" in lf.stem else "?"
        typer.echo(f"{topo:<20} {len(completed):<8} {total_ms:>6}ms   {issues:<8} {ts}")


# ---- why -----------------------------------------------------------------


@app.command()
def why(
    run_id: Annotated[
        str,
        typer.Argument(help="Run log filename or prefix (from swarmkit logs)."),
    ],
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root.", show_default=False),
    ] = Path("."),
) -> None:
    """Explain what happened in a run using an LLM.

    Reads the run's JSONL events, sends them to the configured model
    provider, and returns a plain-English explanation.
    """
    log_dir = workspace_path.resolve() / ".swarmkit" / "logs"
    if not log_dir.is_dir():
        _stderr("No run logs found.")
        raise typer.Exit(_EXIT_USAGE)

    matches = [
        f for f in log_dir.glob("*.jsonl") if f.name.startswith(run_id) or f.stem.startswith(run_id)
    ]
    if not matches:
        _stderr(f"No run log matching '{run_id}'. Use `swarmkit logs` to see available runs.")
        raise typer.Exit(_EXIT_USAGE)

    log_file = sorted(matches, reverse=True)[0]
    events_text = log_file.read_text(encoding="utf-8").strip()

    provider, model = resolve_authoring_provider()

    from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

    prompt = (
        f"Analyze this SwarmKit topology execution log and explain "
        f"what happened.\n\n"
        f"Run log ({log_file.name}):\n{events_text}"
    )
    system = (
        "You are a SwarmKit run analyst. Given a JSONL execution log, "
        "provide a useful analysis covering:\n"
        "1. FLOW: Which agents ran and in what order (root delegates to "
        "leaders, leaders delegate to workers)\n"
        "2. TIMING: Which agents took the longest and why that matters "
        "(is the root bottlenecked synthesising? is a worker slow on "
        "an MCP call?)\n"
        "3. SKILLS: What skills were called, what they produced "
        "(result_length gives a sense of output size)\n"
        "4. ISSUES: Any policy denials, trust failures, or output "
        "validation failures — explain what went wrong and what to fix\n"
        "5. INSIGHT: One actionable observation — e.g., 'the root took "
        "3x longer than the workers, suggesting the synthesis prompt "
        "could be optimised' or 'no workers beyond engineering ran, "
        "the topology may not be delegating to QA/ops'\n\n"
        "Be specific with numbers (cite duration_ms, result_length). "
        "Be concise — aim for 5-8 sentences, not a wall of text. "
        "Don't just describe the log literally — interpret it."
    )
    result = asyncio.run(
        provider.complete(
            CompletionRequest(
                model=model,
                messages=(Message(role="user", content=prompt),),
                system=system,
            )
        )
    )
    parts = [b.text for b in result.content if b.type == "text" and b.text]
    typer.echo("\n".join(parts) or "(no analysis)")


# ---- ask -----------------------------------------------------------------


@app.command()
def ask(
    question: Annotated[
        str,
        typer.Argument(help="Question about the workspace or recent runs."),
    ],
    workspace_path: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace root."),
    ] = Path("."),
) -> None:
    """Ask a question about the workspace or recent runs.

    Single-shot LLM query over the workspace state + recent audit events.
    The conversational counterpart to swarmkit logs.
    """
    ws_root = workspace_path.resolve()

    context_parts = ["# Workspace state"]
    try:
        workspace = resolve_workspace(ws_root)
        context_parts.append(f"Workspace: {workspace.raw.metadata.id}")
        context_parts.append(f"Topologies: {sorted(workspace.topologies.keys())}")
        context_parts.append(f"Skills: {sorted(workspace.skills.keys())}")
        context_parts.append(f"Archetypes: {sorted(workspace.archetypes.keys())}")
    except Exception:
        context_parts.append("(workspace could not be resolved)")

    log_dir = ws_root / ".swarmkit" / "logs"
    if log_dir.is_dir():
        recent = sorted(log_dir.glob("*.jsonl"), reverse=True)[:3]
        if recent:
            context_parts.append("\n# Recent run logs")
            for lf in recent:
                context_parts.append(f"\n## {lf.name}")
                context_parts.append(lf.read_text(encoding="utf-8").strip())

    provider, model = resolve_authoring_provider()

    from swarmkit_runtime.model_providers import CompletionRequest, Message  # noqa: PLC0415

    prompt = (
        f"Context about this SwarmKit workspace:\n\n"
        f"{chr(10).join(context_parts)}\n\n"
        f"Question: {question}"
    )
    result = asyncio.run(
        provider.complete(
            CompletionRequest(
                model=model,
                messages=(Message(role="user", content=prompt),),
                system=(
                    "You are a SwarmKit workspace assistant. You have access "
                    "to the workspace configuration (topologies, skills, "
                    "archetypes) and recent run logs (JSONL events with "
                    "agent timing, skill calls, denials, failures).\n\n"
                    "When answering:\n"
                    "- Cite specific data: agent names, duration_ms, skill IDs\n"
                    "- If asked about performance, compare agent timings\n"
                    "- If asked about failures, explain what went wrong and "
                    "what the user can do about it\n"
                    "- If asked about configuration, reference the actual "
                    "topology/skill/archetype names from the workspace\n"
                    "- Be concise — 3-5 sentences unless the question needs more"
                ),
            )
        )
    )
    parts = [b.text for b in result.content if b.type == "text" and b.text]
    typer.echo("\n".join(parts) or "(no response)")


# ---- knowledge-server ----------------------------------------------------


@app.command(name="knowledge-server")
def knowledge_server(
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            help="Override the repo root (default: auto-detected).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Launch the SwarmKit Knowledge MCP Server (stdio)."""
    from swarmkit_runtime.knowledge._server import run_server  # noqa: PLC0415

    run_server(repo_root=repo.resolve() if repo else None)


# ---- stubs for later milestones ------------------------------------------


@app.command()
def serve(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root directory.", show_default=False),
    ] = Path("."),
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to listen on."),
    ] = 8000,
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind to."),
    ] = "0.0.0.0",
) -> None:
    """Start the SwarmKit HTTP server (design §14.1).

    Loads the workspace and exposes topology execution via REST API.
    Endpoints: GET /health, GET /topologies, GET /skills, POST /run/{topology}.
    """
    import uvicorn  # noqa: PLC0415

    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app_instance = create_app(workspace_path.resolve())
    uvicorn.run(app_instance, host=host, port=port)


@app.command()
def eject(topology: str, output: str = "./generated/") -> None:
    """Export the LangGraph code the runtime would execute (design §14.4)."""
    _not_implemented("eject", milestone="M9 (eject)")


if __name__ == "__main__":
    app()
