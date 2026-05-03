"""The authoring agent — an interactive conversation loop.

Reads from stdin, calls the model, executes tools, prints responses.
See ``design/details/conversational-authoring.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from swarmkit_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Message,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

from ._prompts import AuthoringMode, get_system_prompt
from ._tools import _read_workspace, execute_tool, get_authoring_tools

_MAX_TOOL_ROUNDS = 15
_MAX_WRITE_RETRIES = 3


def run_authoring_session(
    *,
    mode: AuthoringMode,
    model_provider: ModelProviderProtocol,
    model_name: str,
    workspace_path: Path | None = None,
) -> None:
    """Run an interactive authoring session in the terminal."""
    workspace_context = ""
    if workspace_path and workspace_path.exists():
        workspace_context = _read_workspace(str(workspace_path))

    system_prompt = get_system_prompt(mode, workspace_context)
    tools = get_authoring_tools()
    conversation: list[Message] = []

    _print_header(mode)
    if mode == "init":
        _print_agent("What will this swarm do?")

    while True:
        try:
            user_input = _read_input()
        except (EOFError, KeyboardInterrupt):
            print("\n\nAuthoring session ended.")
            break

        if not user_input.strip():
            continue
        if user_input.strip().lower() in ("exit", "quit", "q"):
            print("Authoring session ended.")
            break

        conversation.append(Message(role="user", content=user_input))

        # Detect if user is confirming/approving → force tool calling
        force_tools = _is_confirmation(user_input)

        response = asyncio.run(
            _agent_turn(
                model_provider=model_provider,
                model_name=model_name,
                system_prompt=system_prompt,
                tools=tools,
                conversation=conversation,
                force_tool_call=force_tools,
            )
        )

        agent_text = _extract_text(response)
        if agent_text:
            conversation.append(Message(role="assistant", content=agent_text))
            _print_agent(agent_text)


async def _agent_turn(
    *,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str,
    tools: list[Any],
    conversation: list[Message],
    force_tool_call: bool = False,
) -> CompletionResponse:
    """Run one agent turn, handling tool calls in a loop."""
    messages = list(conversation)
    use_tools: tuple[Any, ...] | None = tuple(tools) if tools else None

    response: CompletionResponse | None = None
    for round_num in range(_MAX_TOOL_ROUNDS):
        # Force tool calling on first round if user confirmed
        extra = {}
        if force_tool_call and round_num == 0 and use_tools:
            extra["tool_choice"] = "required"
        response = await _safe_complete(
            model_provider, model_name, system_prompt, messages, use_tools, extra
        )
        if use_tools is not None and not response.content:
            use_tools = None
            response = await _safe_complete(
                model_provider, model_name, system_prompt, messages, None
            )

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            return response

        for tc in tool_calls:
            result = _handle_tool_call(tc)
            messages.append(Message(role="assistant", content=[tc]))
            messages.append(
                Message(
                    role="tool",
                    content=[
                        ContentBlock(
                            type="tool_result",
                            tool_use_id=tc.tool_use_id,
                            tool_result=result,
                        )
                    ],
                )
            )

    assert response is not None
    return response


async def _safe_complete(
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str,
    messages: list[Message],
    tools: tuple[Any, ...] | None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Call the model, falling back to no-tools on tool-related errors."""
    try:
        return await model_provider.complete(
            CompletionRequest(
                model=model_name,
                messages=tuple(messages),
                system=system_prompt,
                tools=tools,
                extra=extra,
            )
        )
    except Exception:
        if tools is not None:
            return await model_provider.complete(
                CompletionRequest(
                    model=model_name,
                    messages=tuple(messages),
                    system=system_prompt,
                )
            )
        raise


_session_state = {"write_attempt": 0}


def _handle_tool_call(tc: ContentBlock) -> str:
    """Execute a single tool call, with user confirmation for writes."""

    tool_input = tc.tool_input
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            tool_input = {}

    tool_name = tc.tool_name or ""

    if tool_name == "write_files" and isinstance(tool_input, dict):
        files = tool_input.get("files", {})
        if not files:
            return execute_tool(tool_name, tool_input or {})

        _session_state["write_attempt"] += 1
        attempt = _session_state["write_attempt"]
        if attempt == 1:
            _print_agent(_format_file_plan(files))
            if not _read_confirm():
                _session_state["write_attempt"] = 0
                return "User declined. Ask what they'd like to change."

        result = execute_tool(tool_name, tool_input)
        _print_status(result)

        if "validation FAILED" in result:
            if attempt >= _MAX_WRITE_RETRIES:
                _session_state["write_attempt"] = 0
                _print_status(
                    f"  Validation failed after {_MAX_WRITE_RETRIES} attempts. "
                    "Files written but may need manual corrections."
                )
                return result
            _print_status(
                f"  Validation failed (attempt {attempt}/{_MAX_WRITE_RETRIES}). Asking AI to fix..."
            )
            return result

        _session_state["write_attempt"] = 0
        return result

    result = execute_tool(tool_name, tool_input or {})
    if tool_name == "validate_workspace":
        _print_status(f"  validation: {result}")
    return result


def _is_confirmation(text: str) -> bool:
    """Detect if the user's message is a short confirmation/approval."""
    lower = text.strip().lower()
    if len(lower) > 40:
        return False
    exact = {
        "yes",
        "y",
        "ok",
        "sure",
        "confirmed",
        "go ahead",
        "proceed",
        "generate",
        "write",
        "create",
        "looks good",
        "approved",
        "confirm",
        "do it",
        "please",
        "go",
        "yep",
        "yeah",
    }
    return lower in exact


# ---- UI helpers ---------------------------------------------------------


def _extract_text(response: CompletionResponse) -> str:
    return response.text


def _format_file_plan(files: dict[str, str]) -> str:
    color = _use_color()
    lines = ["I'll create these files:\n"]
    for path in sorted(files.keys()):
        if color:
            lines.append(f"  \033[1;32m{path}\033[0m")
        else:
            lines.append(f"  {path}")
    lines.append("\nCreate these files?")
    return "\n".join(lines)


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _print_header(mode: AuthoringMode) -> None:
    titles = {
        "init": "SwarmKit workspace authoring",
        "topology": "SwarmKit topology authoring",
        "skill": "SwarmKit skill authoring",
        "archetype": "SwarmKit archetype authoring",
        "mcp-server": "SwarmKit MCP server authoring",
    }
    title = titles[mode]
    if _use_color():
        print(f"\n\033[1;36m{title}\033[0m — let's build your swarm.")
    else:
        print(f"\n{title} — let's build your swarm.")
    print("Type your message. Ctrl+C to exit.\n")


def _print_agent(text: str) -> None:
    if _use_color():
        print(f"\n\033[0;37m{text}\033[0m\n")
    else:
        print(f"\n{text}\n")


def _print_status(text: str) -> None:
    if _use_color():
        print(f"\033[0;33m{text}\033[0m", file=sys.stderr)
    else:
        print(text, file=sys.stderr)


def _build_author_session() -> Any | None:
    """Build a prompt_toolkit session for authoring."""
    try:
        from prompt_toolkit import PromptSession  # noqa: PLC0415
        from prompt_toolkit.history import FileHistory  # noqa: PLC0415

        history_path = Path.home() / ".swarmkit" / "author_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        return PromptSession(
            history=FileHistory(str(history_path)),
            enable_history_search=True,
        )
    except ImportError:
        return None


_author_session: Any | None = None
_author_session_initialized = False


def _read_input() -> str:
    global _author_session, _author_session_initialized  # noqa: PLW0603
    if not _author_session_initialized:
        _author_session = _build_author_session()
        _author_session_initialized = True
    if _author_session is not None:
        return str(_author_session.prompt("> "))
    return input("> ")


def _read_confirm() -> bool:
    global _author_session, _author_session_initialized  # noqa: PLW0603
    if not _author_session_initialized:
        _author_session = _build_author_session()
        _author_session_initialized = True
    try:
        if _author_session is not None:
            answer = _author_session.prompt("[Y/n] > ").strip().lower()
        else:
            answer = input("[Y/n] > ").strip().lower()
        return answer in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
