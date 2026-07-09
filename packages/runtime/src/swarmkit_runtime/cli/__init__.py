"""SwarmKit CLI — thin interface over WorkspaceRuntime (design §14.2).

Argument parsing and output rendering only; business logic lives in ``WorkspaceRuntime``.

Split out of a single 2332-line module (PR-I3): ``_app`` holds the Typer objects, ``_common``
the shared helpers, and ``_cmd_*`` the command groups (imported below for their @app.command
registration side effects). ``knowledge_pack`` stays here — the test suite monkeypatches
``find_repo_root`` through this package namespace, so its reference must resolve here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

# Import the command modules so their @app.command handlers register on `app`.
from . import (  # noqa: F401
    _cmd_authoring,
    _cmd_chat,
    _cmd_fleet,
    _cmd_misc,
    _cmd_observability,
    _cmd_run,
    _cmd_serve,
)
from ._app import app
from ._cmd_serve import _auth_requires_secure
from ._common import _EXIT_USAGE, _stderr
from ._knowledge import build_pack, find_repo_root

__all__ = ["_auth_requires_secure", "app"]


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


if __name__ == "__main__":
    app()
