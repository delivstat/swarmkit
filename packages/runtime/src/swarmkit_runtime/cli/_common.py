"""Shared CLI helpers — banner, exit codes, stderr, the not-implemented stub, and the
validate-family emitters (errors/success/JSON projections). Split out so every command
module imports them without an ``__init__`` ⇄ command-module cycle."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.resolver import ResolvedWorkspace

from ._render import render_errors, render_success

_BANNER = """\
:'######::'##:::::'##::::'###::::'########::'##::::'##:'##:::'##:'####:'########:
'##... ##: ##:'##: ##:::'## ##::: ##.... ##: ###::'###: ##::'##::. ##::... ##..::
 ##:::..:: ##: ##: ##::'##:. ##:: ##:::: ##: ####'####: ##:'##:::: ##::::: ##::::
. ######:: ##: ##: ##:'##:::. ##: ########:: ## ### ##: #####::::: ##::::: ##::::
:..... ##: ##: ##: ##: #########: ##.. ##::: ##. #: ##: ##. ##:::: ##::::: ##::::
'##::: ##: ##: ##: ##: ##.... ##: ##::. ##:: ##:.:: ##: ##:. ##::: ##::::: ##::::
. ######::. ###. ###:: ##:::: ##: ##:::. ##: ##:::: ##: ##::. ##:'####:::: ##::::
:......::::...::...:::..:::::..::..:::::..::..:::::..::..::::..::....:::::..:::::"""


def _print_banner() -> None:
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        typer.echo(f"\033[1;36m{_BANNER.rstrip()}\033[0m")
    else:
        typer.echo(_BANNER.rstrip())


def _suppress_noisy_logs() -> None:
    """Suppress MCP SDK and third-party INFO messages unless SWARMKIT_VERBOSE is set."""
    if os.environ.get("SWARMKIT_VERBOSE"):
        return
    import logging  # noqa: PLC0415

    for name in ("mcp", "httpx", "httpcore", "sentence_transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)


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
