"""The authoring agent — an interactive conversation loop.

Reads from stdin, calls the model, executes tools, prints responses.
See ``design/details/conversational-authoring.md``.
"""

from __future__ import annotations

import asyncio
import json
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

_MAX_TOOL_ROUNDS = 10


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

        response = asyncio.run(
            _agent_turn(
                model_provider=model_provider,
                model_name=model_name,
                system_prompt=system_prompt,
                tools=tools,
                conversation=conversation,
                workspace_path=workspace_path,
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
    workspace_path: Path | None,
) -> CompletionResponse:
    """Run one agent turn, handling tool calls in a loop."""
    messages = list(conversation)

    response: CompletionResponse | None = None
    for _ in range(_MAX_TOOL_ROUNDS):
        response = await model_provider.complete(
            CompletionRequest(
                model=model_name,
                messages=tuple(messages),
                system=system_prompt,
                tools=tuple(tools),
            )
        )

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            return response

        for tc in tool_calls:
            tool_input = tc.tool_input
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except (json.JSONDecodeError, TypeError):
                    tool_input = {}

            tool_name = tc.tool_name or ""

            if tool_name == "write_files" and tool_input:
                files = tool_input.get("files", {})
                if files:
                    _print_agent(_format_file_plan(files))
                    confirm = _read_confirm()
                    if not confirm:
                        result = "User declined. Ask what they'd like to change."
                    else:
                        result = execute_tool(tool_name, tool_input)
                        _print_status(result)
                else:
                    result = execute_tool(tool_name, tool_input)
            else:
                result = execute_tool(tool_name, tool_input or {})
                if tool_name == "validate_workspace":
                    _print_status(f"  validation: {result}")

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


def _extract_text(response: CompletionResponse) -> str:
    parts = [b.text for b in response.content if b.type == "text" and b.text]
    return "\n".join(parts)


def _format_file_plan(files: dict[str, str]) -> str:
    lines = ["I'll create these files:\n"]
    for path in sorted(files.keys()):
        lines.append(f"  {path}")
    lines.append("\nCreate these files?")
    return "\n".join(lines)


def _print_header(mode: AuthoringMode) -> None:
    titles = {
        "init": "SwarmKit workspace authoring",
        "topology": "SwarmKit topology authoring",
        "skill": "SwarmKit skill authoring",
        "archetype": "SwarmKit archetype authoring",
    }
    print(f"\n{titles[mode]} — let's build your swarm.\n")


def _print_agent(text: str) -> None:
    print(f"\n{text}\n")


def _print_status(text: str) -> None:
    print(text, file=sys.stderr)


def _read_input() -> str:
    return input("> ")


def _read_confirm() -> bool:
    try:
        answer = input("[Y/n] > ").strip().lower()
        return answer in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
