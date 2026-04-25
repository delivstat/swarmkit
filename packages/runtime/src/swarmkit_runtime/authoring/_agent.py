"""The authoring agent — an interactive conversation loop.

Reads from stdin, calls the model, executes tools, prints responses.
See ``design/details/conversational-authoring.md``.
"""

from __future__ import annotations

import asyncio
import json
import re
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

        response = asyncio.run(
            _agent_turn(
                model_provider=model_provider,
                model_name=model_name,
                system_prompt=system_prompt,
                tools=tools,
                conversation=conversation,
            )
        )

        agent_text = _extract_text(response)
        if agent_text:
            # If the model wrote YAML in its text instead of calling write_files,
            # extract the files and offer to write them.
            extracted = _extract_yaml_files(agent_text)
            if extracted and workspace_path:
                _print_agent(agent_text)
                _print_agent(f"I detected {len(extracted)} YAML files in the response.")
                _print_agent(_format_file_plan(extracted))
                if _read_confirm():
                    result = execute_tool(
                        "write_files",
                        {"base_dir": str(workspace_path), "files": extracted},
                    )
                    _print_status(result)
                conversation.append(Message(role="assistant", content=agent_text))
            else:
                conversation.append(Message(role="assistant", content=agent_text))
                _print_agent(agent_text)


async def _agent_turn(
    *,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str,
    tools: list[Any],
    conversation: list[Message],
) -> CompletionResponse:
    """Run one agent turn, handling tool calls in a loop."""
    messages = list(conversation)
    use_tools: tuple[Any, ...] | None = tuple(tools) if tools else None

    response: CompletionResponse | None = None
    for _ in range(_MAX_TOOL_ROUNDS):
        response = await _safe_complete(
            model_provider, model_name, system_prompt, messages, use_tools
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
) -> Any:
    """Call the model, falling back to no-tools on tool-related errors."""
    try:
        return await model_provider.complete(
            CompletionRequest(
                model=model_name,
                messages=tuple(messages),
                system=system_prompt,
                tools=tools,
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


_write_attempt = 0


def _handle_tool_call(tc: ContentBlock) -> str:
    """Execute a single tool call, with user confirmation for writes."""
    global _write_attempt  # noqa: PLW0603

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

        _write_attempt += 1
        if _write_attempt == 1:
            _print_agent(_format_file_plan(files))
            if not _read_confirm():
                _write_attempt = 0
                return "User declined. Ask what they'd like to change."

        result = execute_tool(tool_name, tool_input)
        _print_status(result)

        if "validation FAILED" in result:
            if _write_attempt >= _MAX_WRITE_RETRIES:
                _write_attempt = 0
                _print_status(
                    f"  ✗ Validation failed after {_MAX_WRITE_RETRIES} attempts. "
                    "Files written but may need manual corrections."
                )
                return result
            _print_status(
                f"  ↻ Validation failed (attempt {_write_attempt}/{_MAX_WRITE_RETRIES}). "
                "Asking AI to fix..."
            )
            return result

        _write_attempt = 0
        return result

    result = execute_tool(tool_name, tool_input or {})
    if tool_name == "validate_workspace":
        _print_status(f"  validation: {result}")
    return result


# ---- YAML extraction from text ------------------------------------------


def _extract_yaml_files(text: str) -> dict[str, str] | None:
    """Extract YAML file contents from model text output.

    Detects patterns like:
      **`path/to/file.yaml`**
      ```yaml
      content...
      ```
    or:
      # path/to/file.yaml
      ```yaml
      content...
      ```

    Returns a dict of {relative_path: yaml_content} if files found.
    """

    files: dict[str, str] = {}

    # Pattern: **`filename.yaml`** followed by ```yaml block
    # or: # filename.yaml comment inside ```yaml block
    blocks = re.split(r"```ya?ml\s*\n", text)
    if len(blocks) < 2:
        return None

    for i in range(1, len(blocks)):
        yaml_content = blocks[i].split("```")[0].strip()
        if not yaml_content or "apiVersion:" not in yaml_content:
            continue

        # Look for filename in the text before this code block
        preceding = blocks[i - 1]
        path = _find_filename(preceding, yaml_content)
        if path:
            files[path] = yaml_content + "\n"

    return files if files else None


def _find_filename(preceding_text: str, yaml_content: str) -> str | None:
    """Extract a .yaml filename from text preceding a code block."""
    # Match **`path/file.yaml`** or `path/file.yaml`
    matches = re.findall(r"`([^`]*\.ya?ml)`", preceding_text)
    if matches:
        path = matches[-1]
        parts = path.split("/")
        if len(parts) > 1 and parts[0].endswith("-swarm"):
            path = "/".join(parts[1:])
        return str(path)

    # Match # path/file.yaml comment
    matches = re.findall(r"#\s*(\S+\.ya?ml)", preceding_text)
    if matches:
        return str(matches[-1])

    # Infer from YAML content
    return _infer_filename_from_content(yaml_content)


def _infer_filename_from_content(yaml_content: str) -> str | None:
    """Guess filename from YAML kind + id/name fields."""
    kind_map = {
        "Workspace": ("", "workspace"),
        "Topology": ("topologies", "name"),
        "Archetype": ("archetypes", "id"),
        "Skill": ("skills", "id"),
    }
    for kind, (subdir, field) in kind_map.items():
        if f"kind: {kind}" not in yaml_content:
            continue
        if kind == "Workspace":
            return "workspace.yaml"
        match = re.search(rf"{field}:\s*(\S+)", yaml_content)
        name = match.group(1) if match else kind.lower()
        return f"{subdir}/{name}.yaml"
    return None


# ---- UI helpers ---------------------------------------------------------


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
        "mcp-server": "SwarmKit MCP server authoring",
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
