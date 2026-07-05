"""CLI commands — the interactive chat REPL + conversation listing/resume."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    pass

import typer

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
)
from swarmkit_runtime.errors import ResolutionErrors

from ._app import app
from ._common import (
    _EXIT_RESOLUTION_ERROR,
    _EXIT_USAGE,
    _print_banner,
    _stderr,
    _suppress_noisy_logs,
)

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

    _print_banner()
    _suppress_noisy_logs()

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
            "/clear",
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


async def _async_conversation_loop(conv: Any, manager: Any) -> None:  # noqa: PLR0912, PLR0915
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

            # /clear command — reset conversation context and tool cache
            if user_input.strip() == "/clear":
                conv.clear()
                if hasattr(manager, "_runtime") and hasattr(manager._runtime, "_mcp_manager"):
                    mcp = manager._runtime._mcp_manager
                    if mcp:
                        mcp.clear_cache()
                typer.echo("Conversation cleared. Starting fresh (MCP servers still running).")
                continue

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
