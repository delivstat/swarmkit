"""Text rendering for ``swarmkit validate`` output.

Separated from the Typer wiring so renderers are unit-testable without
spinning up the CLI. See ``design/details/swarmkit-validate-cli.md`` for
the spec.
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Sequence
from pathlib import Path

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedWorkspace

# ANSI colour helpers. We emit them inline when colour is enabled and
# strip the wrapper when it isn't. No rich/typer dependency — we want
# rendering to be usable from hand-written tests.
_RESET = "\x1b[0m"
_RED = "\x1b[31m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"


def _c(text: str, code: str, *, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def should_colour(stream_is_tty: bool, color_override: bool | None) -> bool:
    """Decide whether to emit ANSI codes.

    ``color_override`` wins if set. ``NO_COLOR`` env var suppresses
    (https://no-color.org). Otherwise default to TTY presence.
    """
    if color_override is not None:
        return color_override
    if os.environ.get("NO_COLOR"):
        return False
    return stream_is_tty


# ---- error rendering ----------------------------------------------------


def render_error(
    err: ResolutionError,
    *,
    workspace_root: Path | None = None,
    color: bool = False,
) -> str:
    """Render one :class:`ResolutionError` as a human-readable block."""
    title = _c("error", _RED + _BOLD, color=color) + ": " + err.message
    lines = [title]

    path_display = _relpath(err.artifact_path, workspace_root)
    pointer = err.yaml_pointer or ""
    at_line = _c("at", _DIM, color=color) + "  " + path_display
    lines.append(f"  {at_line}")
    if pointer:
        lines.append(f"      {pointer}")

    rule = err.rule or err.code
    rule_line = _c("rule", _DIM, color=color) + "  " + rule
    lines.append(f"  {rule_line}")

    if err.suggestion:
        suggestion = _format_multiline(err.suggestion, indent=8)
        try_line = _c("try", _DIM, color=color) + "   " + suggestion.lstrip()
        lines.append(f"  {try_line}")

    for related in err.related:
        lines.append("")
        lines.append(render_error(related, workspace_root=workspace_root, color=color))

    return "\n".join(lines)


def render_errors(
    errors: Sequence[ResolutionError],
    *,
    workspace_root: Path | None = None,
    color: bool = False,
) -> str:
    """Render a list of errors separated by blank lines plus a summary."""
    blocks = [render_error(err, workspace_root=workspace_root, color=color) for err in errors]
    n_files = len({err.artifact_path for err in errors})
    summary = _summary_line(len(errors), n_files)
    return "\n\n".join(blocks) + "\n\n" + summary


def _summary_line(n_errors: int, n_files: int) -> str:
    files_word = "file" if n_files == 1 else "files"
    errors_word = "error" if n_errors == 1 else "errors"
    return (
        f"{n_errors} {errors_word} across {n_files} {files_word}. See "
        "design/details/topology-loader.md for the error-code reference."
    )


def _format_multiline(text: str, *, indent: int) -> str:
    wrapped = textwrap.dedent(text).strip()
    lines = wrapped.splitlines()
    if not lines:
        return wrapped
    padding = " " * indent
    return lines[0] + "\n" + "\n".join(padding + ln for ln in lines[1:])


def _relpath(path: Path, workspace_root: Path | None) -> str:
    if workspace_root is not None:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            pass
    return str(path)


# ---- success rendering --------------------------------------------------


def render_success(
    ws: ResolvedWorkspace,
    *,
    tree: bool = False,
    color: bool = False,
) -> str:
    """Render the success summary for a valid workspace.

    ``tree=True`` additionally prints a per-topology agent tree.
    """
    ok = _c("✓", _BOLD, color=color)
    header = f"{ok} workspace: {_identifier(ws.raw.metadata.id)}"
    topo_ids = ", ".join(sorted(ws.topologies.keys())) or "—"
    skill_ids = ", ".join(sorted(ws.skills.keys())) or "—"
    arch_ids = ", ".join(sorted(ws.archetypes.keys())) or "—"
    trigger_ids = ", ".join(sorted(t.id for t in ws.triggers)) or "—"

    lines = [
        header,
        f"  topologies: {len(ws.topologies):<3} ({topo_ids})",
        f"  skills:     {len(ws.skills):<3} ({skill_ids})",
        f"  archetypes: {len(ws.archetypes):<3} ({arch_ids})",
        f"  triggers:   {len(ws.triggers):<3} ({trigger_ids})",
        "",
        "no errors, 0 warnings",
    ]

    if tree:
        lines.append("")
        for topology_id in sorted(ws.topologies.keys()):
            topology = ws.topologies[topology_id]
            lines.append(_c(f"topology: {topology_id}", _BOLD, color=color))
            lines.extend(_render_agent_tree(topology.root, indent=2, color=color))
            lines.append("")

    return "\n".join(lines)


def _identifier(value: object) -> str:
    root = getattr(value, "root", value)
    return str(root)


def _render_agent_tree(
    agent: ResolvedAgent,
    *,
    indent: int,
    color: bool,
) -> list[str]:
    pad = " " * indent
    head = f"{pad}{agent.id} (role={agent.role}"
    if agent.source_archetype:
        head += f", archetype={agent.source_archetype}"
    head += ")"
    lines: list[str] = [head]

    if agent.model:
        provider = agent.model.get("provider", "?")
        name = agent.model.get("name", "?")
        lines.append(f"{pad}  model: {provider}/{name}")
    if agent.skills:
        ids = ", ".join(s.id for s in agent.skills) or "—"
        lines.append(f"{pad}  skills: {ids}")
    if agent.iam:
        scopes = agent.iam.get("base_scope")
        if scopes:
            lines.append(f"{pad}  iam.base_scope: {list(scopes)}")
    for child in agent.children:
        lines.extend(_render_agent_tree(child, indent=indent + 2, color=color))
    return lines


__all__ = [
    "render_error",
    "render_errors",
    "render_success",
    "should_colour",
]
