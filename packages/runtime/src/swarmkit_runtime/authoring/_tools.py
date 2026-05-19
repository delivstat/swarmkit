"""Tools available to the authoring agent.

These are SwarmKit-internal tools, not MCP — they run in-process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.model_providers import ToolSpec
from swarmkit_runtime.resolver import resolve_workspace


def get_authoring_tools() -> list[ToolSpec]:
    """Return the tool definitions the authoring agent can call."""
    return [
        ToolSpec(
            name="validate_workspace",
            description=(
                "Validate a workspace directory. Returns 'valid' or a list of "
                "errors with suggestions. Call this after generating YAML to "
                "check correctness before asking the user to approve."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workspace_path": {
                        "type": "string",
                        "description": "Path to the workspace directory",
                    },
                },
                "required": ["workspace_path"],
            },
        ),
        ToolSpec(
            name="write_files",
            description=(
                "Write YAML files to disk. Only call after the user explicitly "
                "approves. Each entry is a relative path and its YAML content."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "base_dir": {
                        "type": "string",
                        "description": "Base directory to write files into",
                    },
                    "files": {
                        "type": "object",
                        "description": "Map of relative file path → YAML content",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["base_dir", "files"],
            },
        ),
        ToolSpec(
            name="read_workspace",
            description=(
                "Read the current state of a workspace directory. Returns the "
                "workspace.yaml content and lists of existing topologies, "
                "archetypes, and skills."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workspace_path": {
                        "type": "string",
                        "description": "Path to the workspace directory",
                    },
                },
                "required": ["workspace_path"],
            },
        ),
    ]


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Execute an authoring tool and return the result as a string."""
    if tool_name == "validate_workspace":
        return _validate_workspace(tool_input.get("workspace_path", "."))
    if tool_name == "write_files":
        return _write_files(
            tool_input.get("base_dir", "."),
            tool_input.get("files", {}),
        )
    if tool_name == "read_workspace":
        return _read_workspace(tool_input.get("workspace_path", "."))
    return f"Unknown tool: {tool_name}"


def _validate_workspace(workspace_path: str) -> str:
    path = Path(workspace_path).resolve()
    if not path.exists():
        return f"Workspace path does not exist: {path}"
    try:
        ws = resolve_workspace(path)
        topo_count = len(ws.topologies)
        skill_count = len(ws.skills)
        arch_count = len(ws.archetypes)
        return (
            f"valid — {topo_count} topologies, {skill_count} skills, "
            f"{arch_count} archetypes, 0 errors."
        )
    except ResolutionErrors as exc:
        lines = []
        for err in exc.errors:
            lines.append(f"error: {err.message}")
            if err.suggestion:
                lines.append(f"  try: {err.suggestion}")
        return "\n".join(lines)
    except FileNotFoundError as exc:
        return f"error: {exc}"


def _write_files(base_dir: str, files: dict[str, str]) -> str:
    base = Path(base_dir).resolve()
    written: list[str] = []
    for rel_path, content in files.items():
        full_path = base / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        written.append(rel_path)

    validation = _validate_workspace(str(base))
    if "valid" in validation and "0 errors" in validation:
        return f"Wrote {len(written)} files: {', '.join(written)}. {validation}"

    return (
        f"Wrote {len(written)} files but validation FAILED.\n"
        f"{validation}\n"
        f"Fix the errors above and call write_files again with corrected content."
    )


def _read_workspace(workspace_path: str) -> str:
    path = Path(workspace_path).resolve()
    if not path.exists():
        return f"Workspace path does not exist: {path}"

    lines: list[str] = []

    ws_yaml = path / "workspace.yaml"
    if ws_yaml.exists():
        lines.append(f"workspace.yaml:\n{ws_yaml.read_text(encoding='utf-8')}")
    else:
        lines.append("workspace.yaml: not found")

    for subdir in ("topologies", "archetypes", "skills"):
        sub = path / subdir
        if sub.is_dir():
            yamls = sorted(sub.glob("*.yaml"))
            names = [f.stem for f in yamls]
            lines.append(f"{subdir}/: {', '.join(names) or '(empty)'}")
        else:
            lines.append(f"{subdir}/: (not created)")

    return "\n".join(lines)
