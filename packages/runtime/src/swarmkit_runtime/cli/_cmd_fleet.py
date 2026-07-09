"""``swarmkit fleet`` — the instance owner's side of enrollment (design 19).

The owner mints a one-time **enrollment token** on the instance and hands it to a fleet operator,
who pastes it into the fleet UI's Register action. This is the human gate for a ``manage``-scope
join. The commands operate directly on the instance's membership store (``.swarmkit/fleet.sqlite``)
— no running serve, no auth token — because the owner already has the workspace on disk (like
``swarmkit auth token``). See design/details/control-plane/19-fleet-enrollment-protocol.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ._app import fleet_app

_SCOPES = ("monitor", "manage")


@fleet_app.command("enroll-token")
def enroll_token(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root directory (holds .swarmkit/fleet.sqlite)."),
    ] = Path("."),
    scope: Annotated[
        str,
        typer.Option("--scope", help="Access the fleet gets: monitor (observe) | manage (deploy)."),
    ] = "monitor",
    ttl: Annotated[
        int,
        typer.Option("--ttl", help="Seconds the token stays valid before it expires."),
    ] = 900,
) -> None:
    """Mint a one-time enrollment token for a fleet to register with this instance.

    Print a single-use code (valid for --ttl seconds) the fleet operator pastes into the fleet UI's
    'Register' action. A ``manage`` token grants deploy rights, so minting it is a deliberate human
    action — that is the whole point of running this by hand.
    """
    if scope not in _SCOPES:
        typer.echo(f"invalid scope '{scope}' — use {' | '.join(_SCOPES)}", err=True)
        raise typer.Exit(code=2)

    from swarmkit_runtime.fleet import create_membership_store  # noqa: PLC0415
    from swarmkit_runtime.fleet._credentials import Scope  # noqa: PLC0415

    store = create_membership_store(workspace_path.resolve())
    token_scope: Scope = scope  # type: ignore[assignment]
    token = store.create_enrollment_token(token_scope, ttl_seconds=ttl)

    typer.echo(f"# Enrollment token (scope: {scope}, valid {ttl}s, single-use):")
    typer.echo(token)
    typer.echo("")
    typer.echo("# Hand this to the fleet operator. In the fleet UI, open this instance and use")
    typer.echo("# 'Register' (Fleet enrollment) — paste the token there. Works once, then expires.")
    if scope == "manage":
        typer.echo("# NOTE: 'manage' lets the fleet deploy artifacts to this instance.")


@fleet_app.command("memberships")
def memberships(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root directory (holds .swarmkit/fleet.sqlite)."),
    ] = Path("."),
) -> None:
    """List the fleets registered with this instance (no secrets). Shows each membership's scope,
    key fingerprint, and whether the fleet's identity is pinned (design 21)."""
    from swarmkit_runtime.fleet import create_membership_store  # noqa: PLC0415

    store = create_membership_store(workspace_path.resolve())
    rows = store.list_memberships()
    if not rows:
        typer.echo("No fleets have registered with this instance yet.")
        return
    typer.echo(f"{'FLEET_ID':<48}  {'SCOPE':<8}  {'FINGERPRINT':<14}  IDENTITY")
    for m in rows:
        pinned = "pinned" if store.get_fleet_key(m.fleet_id) else "unpinned"
        typer.echo(f"{m.fleet_id:<48}  {m.scope:<8}  {m.key_fingerprint:<14}  {pinned}")
