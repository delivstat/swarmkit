"""Tools the authoring agent can call.

Each tool is a thin dispatcher that shells out to a command_pack script shipped inside
the bundled authoring workspace (``swarmkit_runtime.authoring_workspace.command_packs.
author-tools``). The scripts enforce the IAM scope (``write-file`` refuses paths
outside the target workspace's allowed roots) and the ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``
env var pins the target — the model never names it, so a bad prompt cannot steer writes
to another directory.

Keeping the tools scripts-and-subprocess (not in-process Python) matters: the compiler
calls these same scripts the same way once the CLI shim swap lands (PR 4+ of #1045), so
the behaviour verified here is the behaviour the serve path will have, byte for byte.
See ``design/details/author-bundled-workspace.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from swarmkit_runtime.authoring._resolver import get_authoring_workspace_path
from swarmkit_runtime.model_providers import ToolSpec

_SCRIPT_DIR: Path = get_authoring_workspace_path() / "command_packs" / "author-tools"

#: Tool name → script file. Tool names use underscores (Python/Anthropic tool-call
#: convention); the matching skill ids use hyphens (SwarmKit YAML convention). The LLM
#: only ever sees the tool name on the left; the compiler maps between the two.
_TOOL_TO_SCRIPT: dict[str, str] = {
    "write_file": "write_file.py",
    "read_workspace": "read_workspace.py",
    "validate_workspace": "validate_workspace.py",
    "search_skills_catalogue": "search_skills_catalogue.py",
}


def get_authoring_tools() -> list[ToolSpec]:
    """Return the tool definitions the authoring agent can call.

    Shapes match the inputs schemas on the bundled skills under
    ``authoring_workspace/skills/``. If you change a shape here, change it there too —
    once the CLI shim swap lands, the compiler reads the skill YAML directly and this
    list goes away.
    """
    return [
        ToolSpec(
            name="write_file",
            description=(
                "Write YAML/JSON files into the target workspace. Paths are "
                "relative; only topologies/, archetypes/, skills/, funnels/, "
                "schemas/, policies/ and workspace.yaml are allowed. Only call "
                "after the user has approved the plan."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["files"],
                "properties": {
                    "files": {
                        "type": "object",
                        "description": ("Map of workspace-relative path to file content."),
                        "additionalProperties": {"type": "string"},
                        "minProperties": 1,
                    },
                },
            },
        ),
        ToolSpec(
            name="read_workspace",
            description=(
                "Return the target workspace's workspace.yaml text + a per-kind "
                "inventory of top-level YAMLs. Call this at the start of a "
                "session to avoid proposing an artifact that already exists."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        ),
        ToolSpec(
            name="validate_workspace",
            description=(
                "Validate the target workspace against the canonical schemas + "
                "resolver. Call after write_file to confirm the generated set "
                "is consistent."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        ),
        ToolSpec(
            name="search_skills_catalogue",
            description=(
                "Look up a bundle in the swarmkit-skills catalogue by keyword. "
                "When a bundle covers the ask, suggest `swarmkit skill add "
                "bundle:<id>` instead of hand-rolling a custom skill or MCP "
                "server."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                },
            },
        ),
    ]


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    target_workspace: Path | None = None,
) -> str:
    """Execute an authoring tool and return the result as a string.

    ``target_workspace`` is passed through to the script as
    ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``. The caller (``_agent.py``) threads the session
    workspace in. If the tool is unknown we return a short message instead of raising —
    the model occasionally asks for a tool that doesn't exist and the agent loop
    handles it gracefully.
    """
    script = _TOOL_TO_SCRIPT.get(tool_name)
    if script is None:
        return f"Unknown tool: {tool_name}"

    env = os.environ.copy()
    if target_workspace is not None:
        env["SWARMKIT_AUTHOR_TARGET_WORKSPACE"] = str(target_workspace.resolve())

    try:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_DIR / script)],
            input=json.dumps(tool_input or {}),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except OSError as exc:  # pragma: no cover — only hits on a broken python exe
        return f"tool invocation failed: {exc}"

    # Scripts always print one JSON line to stdout; return it verbatim so the agent can
    # thread the raw structured response back to the model (the model handles the JSON
    # better than it handles a free-form summary).
    out = proc.stdout.strip()
    if proc.returncode == 0:
        return out or "{}"
    # Non-zero exit — still return the JSON so the model sees `{"error": ..., "detail": ...}`
    # and can self-correct. Preserve stderr too in case the script crashed before
    # printing the JSON line.
    if out:
        return out
    return f"tool error (exit {proc.returncode}): {proc.stderr.strip() or 'no output'}"


def _read_workspace(workspace_path: str) -> str:
    """Return the workspace summary the agent uses as its session-start context.

    Called once at the top of a run — ``_agent.py`` appends the returned string to the
    system prompt so the model can see what already exists. We shell out to the same
    script the ``read_workspace`` tool uses and reformat its JSON into the one-screen
    human summary the prompt expects.
    """
    path = Path(workspace_path).resolve()
    if not path.exists():
        return f"Workspace path does not exist: {path}"
    raw = execute_tool("read_workspace", {}, target_workspace=path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if "error" in data:
        return f"error: {data.get('detail') or data['error']}"
    lines: list[str] = []
    ws_yaml = data.get("workspace_yaml") or ""
    if ws_yaml:
        lines.append(f"workspace.yaml:\n{ws_yaml}")
    else:
        lines.append("workspace.yaml: not found")
    for key in ("topologies", "archetypes", "skills", "funnels"):
        names = data.get(key) or []
        lines.append(f"{key}/: {', '.join(names) or '(empty)'}")
    return "\n".join(lines)
