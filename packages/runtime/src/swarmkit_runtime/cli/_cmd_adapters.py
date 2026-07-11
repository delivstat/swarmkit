"""``swarmkit adapters`` — inspect and approve declarative harness adapters (executor P3 PR6).

The launch block of a workspace adapter is a command line run on the host, so it carries a
mandatory human-review gate: a run refuses until the launch surface is approved, and re-approval is
required on any change. Approval is a human action recorded on disk — no agent can grant it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime.executors import (
    approve_launch,
    is_launch_approved,
    launch_fingerprint,
    load_adapter_specs,
    load_workspace_adapter_specs,
)

from ._app import adapters_app


@adapters_app.command("list")
def adapters_list(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """List every available adapter kind and, for workspace adapters, its launch-approval status."""
    root = workspace_path.resolve()
    all_specs = load_adapter_specs(root)
    workspace_kinds = set(load_workspace_adapter_specs(root))
    if not all_specs:
        typer.echo("No adapters found.")
        return
    for kind in sorted(all_specs):
        if kind in workspace_kinds:
            status = "approved" if is_launch_approved(root, all_specs[kind]) else "NEEDS REVIEW"
            source = "workspace"
        else:
            status = "pre-vetted"
            source = "bundled"
        typer.echo(f"  {kind:<20} {source:<10} {status}")


@adapters_app.command("show")
def adapters_show(
    kind: Annotated[str, typer.Argument(help="Adapter kind (id).")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Show an adapter's launch command + fingerprint — what a reviewer inspects before approval."""
    root = workspace_path.resolve()
    spec = load_adapter_specs(root).get(kind)
    if spec is None:
        typer.echo(f"Unknown adapter kind: {kind!r}")
        raise typer.Exit(code=1)
    typer.echo(f"kind:        {spec.kind}")
    typer.echo(f"launch:      {' '.join(spec.launch.command)}")
    for group in spec.launch.optional_args:
        typer.echo(f"  + when {group.when}: {' '.join(group.args)}")
    if spec.launch.env:
        typer.echo(f"env:         {dict(spec.launch.env)}")
    for name, mode in sorted(spec.auth.modes.items()):
        typer.echo(f"auth[{name}]:  env={dict(mode.env)} args={list(mode.args)}")
    typer.echo(f"fingerprint: {launch_fingerprint(spec)}")


@adapters_app.command("approve")
def adapters_approve(
    kind: Annotated[str, typer.Argument(help="Adapter kind (id) to approve.")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Approve a workspace adapter's current launch block (a human action). Inspect it with
    ``swarmkit adapters show <kind>`` first."""
    root = workspace_path.resolve()
    workspace_specs = load_workspace_adapter_specs(root)
    spec = workspace_specs.get(kind)
    if spec is None:
        typer.echo(
            f"{kind!r} is not a workspace adapter (bundled adapters are pre-vetted and need no "
            f"approval). Workspace adapters: {sorted(workspace_specs) or 'none'}"
        )
        raise typer.Exit(code=1)
    fingerprint = approve_launch(root, spec)
    typer.echo(f"✓ Approved launch of {kind!r}\n  {fingerprint}")
