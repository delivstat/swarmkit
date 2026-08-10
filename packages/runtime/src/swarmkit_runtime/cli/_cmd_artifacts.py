"""CLI: ``swarmkit artifacts`` — list and fetch run outputs by correlation id.

Read-only, thin over the workspace's configured `ArtifactStore` (database / filesystem / s3). The
store has existed since the pipeline orchestrator shipped, but only pipeline stage code wrote to it
and nothing read it back from a terminal — so a one-shot run's output had nowhere durable to go that
was not a shell redirect, and anything written was write-only.

Refs are ``<correlation_id>/<stage-or-run>/<name>``. A pipeline stage puts its stage id in the
middle segment; a one-shot `swarmkit run --save-artifact` puts the RUN id there, which is also
`jobs.id` and `audit_events.run_id` — so a reader holding one holds the others.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ._app import artifacts_app

_PathArg = Annotated[Path, typer.Option("--workspace", "-w", help="Workspace root.")]


def _store(workspace: Path) -> object:
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    return storage_for_workspace(workspace.resolve()).artifact_store()


@artifacts_app.command("list")
def list_artifacts(
    correlation_id: Annotated[str, typer.Argument(help="The correlation id to list under.")],
    workspace: _PathArg = Path("."),
) -> None:
    """List artifact refs recorded under one correlation id."""
    refs = _store(workspace).list(correlation_id)  # type: ignore[attr-defined]
    if not refs:
        typer.echo(f"no artifacts under {correlation_id!r}")
        return
    for ref in refs:
        typer.echo(ref)


@artifacts_app.command("get")
def get_artifact(
    ref: Annotated[str, typer.Argument(help="Full ref: <correlation>/<stage-or-run>/<name>.")],
    workspace: _PathArg = Path("."),
) -> None:
    """Print one artifact's content.

    Exits non-zero when the ref resolves to nothing, so a shell pipeline does not silently carry an
    empty string forward as though it were the output.
    """
    content = _store(workspace).get(ref)  # type: ignore[attr-defined]
    if content is None:
        typer.echo(f"error: no artifact at {ref!r}", err=True)
        raise typer.Exit(1)
    sys.stdout.write(content)
