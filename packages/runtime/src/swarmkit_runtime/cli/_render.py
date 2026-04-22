"""Text rendering for ``swarmkit validate`` output.

Separated from the Typer wiring so renderers are unit-testable without
spinning up the CLI. See ``design/details/swarmkit-validate-cli.md``.
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Iterable, Sequence
from pathlib import Path

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedWorkspace

# ANSI is emitted inline when colour is enabled and stripped when not —
# no rich/typer dependency so renderers stay usable from plain tests.
_RESET = "\x1b[0m"
_RED = "\x1b[31m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"


def _c(text: str, code: str, *, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _label(text: str, *, color: bool) -> str:
    return _c(text, _DIM, color=color)


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
    """Render one :class:`ResolutionError` as a human-readable block.

    Format::

        error: <message>
          at    <path>
                <pointer>
          rule  <rule-or-code>
          try   <suggestion>
          <related errors, blank-line-separated>
    """
    lines: list[str] = [_title_line(err, color)]
    lines.extend(_location_lines(err, workspace_root, color))
    lines.append(_rule_line(err, color))
    lines.extend(_suggestion_lines(err, color))
    lines.extend(_related_lines(err, workspace_root, color))
    return "\n".join(lines)


def render_errors(
    errors: Sequence[ResolutionError],
    *,
    workspace_root: Path | None = None,
    color: bool = False,
) -> str:
    """Render a list of errors separated by blank lines plus a summary."""
    blocks = [render_error(e, workspace_root=workspace_root, color=color) for e in errors]
    summary = _summary_line(len(errors), n_files=len({e.artifact_path for e in errors}))
    return "\n\n".join(blocks) + "\n\n" + summary


def _title_line(err: ResolutionError, color: bool) -> str:
    return f"{_c('error', _RED + _BOLD, color=color)}: {err.message}"


def _location_lines(err: ResolutionError, workspace_root: Path | None, color: bool) -> list[str]:
    path = _relpath(err.artifact_path, workspace_root)
    lines = [f"  {_label('at', color=color)}  {path}"]
    if err.yaml_pointer:
        lines.append(f"      {err.yaml_pointer}")
    return lines


def _rule_line(err: ResolutionError, color: bool) -> str:
    return f"  {_label('rule', color=color)}  {err.rule or err.code}"


def _suggestion_lines(err: ResolutionError, color: bool) -> list[str]:
    if not err.suggestion:
        return []
    body = _format_multiline(err.suggestion, indent=8).lstrip()
    return [f"  {_label('try', color=color)}   {body}"]


def _related_lines(err: ResolutionError, workspace_root: Path | None, color: bool) -> list[str]:
    lines: list[str] = []
    for rel in err.related:
        lines.append("")
        lines.append(render_error(rel, workspace_root=workspace_root, color=color))
    return lines


def _summary_line(n_errors: int, *, n_files: int) -> str:
    errors_word = "error" if n_errors == 1 else "errors"
    files_word = "file" if n_files == 1 else "files"
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
    lines: list[str] = [_success_header(ws, color)]
    lines.extend(_registry_count_lines(ws))
    lines.append("")
    lines.append("no errors, 0 warnings")
    if tree:
        lines.append("")
        lines.extend(_topology_tree_lines(ws, color))
    return "\n".join(lines)


def _success_header(ws: ResolvedWorkspace, color: bool) -> str:
    ok = _c("✓", _BOLD, color=color)
    return f"{ok} workspace: {_identifier(ws.raw.metadata.id)}"


def _registry_count_lines(ws: ResolvedWorkspace) -> list[str]:
    kinds: list[tuple[str, list[str]]] = [
        ("topologies", sorted(ws.topologies.keys())),
        ("skills", sorted(ws.skills.keys())),
        ("archetypes", sorted(ws.archetypes.keys())),
        ("triggers", sorted(t.id for t in ws.triggers)),
    ]
    return [_count_line(label, ids) for label, ids in kinds]


def _count_line(label: str, ids: list[str]) -> str:
    listing = ", ".join(ids) or "—"
    # Pad "<label>:" to 12 chars so counts and listings align across rows.
    return f"  {label + ':':<12}{len(ids):<3} ({listing})"


def _topology_tree_lines(ws: ResolvedWorkspace, color: bool) -> list[str]:
    lines: list[str] = []
    for topology_id in sorted(ws.topologies.keys()):
        topology = ws.topologies[topology_id]
        lines.append(_c(f"topology: {topology_id}", _BOLD, color=color))
        lines.extend(_agent_tree_lines(topology.root, indent=2, color=color))
        lines.append("")
    return lines


def _agent_tree_lines(agent: ResolvedAgent, *, indent: int, color: bool) -> list[str]:
    pad = " " * indent
    lines: list[str] = [_agent_head(agent, pad)]
    lines.extend(_agent_attribute_lines(agent, pad))
    for child in agent.children:
        lines.extend(_agent_tree_lines(child, indent=indent + 2, color=color))
    return lines


def _agent_head(agent: ResolvedAgent, pad: str) -> str:
    head = f"{pad}{agent.id} (role={agent.role}"
    if agent.source_archetype:
        head += f", archetype={agent.source_archetype}"
    return head + ")"


def _agent_attribute_lines(agent: ResolvedAgent, pad: str) -> list[str]:
    lines: list[str] = []
    if agent.model:
        provider = agent.model.get("provider", "?")
        name = agent.model.get("name", "?")
        lines.append(f"{pad}  model: {provider}/{name}")
    if agent.skills:
        lines.append(f"{pad}  skills: {_csv(s.id for s in agent.skills)}")
    if agent.iam:
        scopes = agent.iam.get("base_scope")
        if scopes:
            lines.append(f"{pad}  iam.base_scope: {list(scopes)}")
    return lines


def _csv(items: Iterable[str]) -> str:
    return ", ".join(items) or "—"


def _identifier(value: object) -> str:
    root = getattr(value, "root", value)
    return str(root)


__all__ = [
    "render_error",
    "render_errors",
    "render_success",
    "should_colour",
]
