"""``swarmkit upgrade`` — upgrade the local install, with a breaking-change gate.

Thin over ``swarmkit_runtime._upgrade`` and ``_breaking_changes`` (upgrade-command.md): detect how
the runtime was installed and with which extras, find the newest version (or ``--to``), show any
breaking changes in between, and ask before running the install. ``--check`` reports without acting.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ._app import app
from ._common import _EXIT_USAGE, _stderr


def _print_plan(installed: str, target: str, method: str, extras: tuple[str, ...]) -> list[Any]:
    from swarmkit_runtime._breaking_changes import changes_between  # noqa: PLC0415

    typer.echo(f"upgrade: swarmkit-runtime {installed} → {target}")
    typer.echo(f"  install method: {method} · extras: {', '.join(extras) or 'none'}")
    breaks = changes_between(installed, target)
    if breaks:
        typer.echo("")
        typer.echo(f"⚠ {len(breaks)} breaking change(s) in this range:")
        for b in breaks:
            typer.echo(f"  {b.version}  {b.summary}")
            typer.echo(f"    migration: {b.migration}")
    return breaks


def _refuse_undriven(method: str, extras: tuple[str, ...], to: str | None) -> None:
    from swarmkit_runtime import _upgrade as up  # noqa: PLC0415

    typer.echo("")
    if method == "docker":
        _stderr(
            "This is a container image — upgrade by pulling a newer tag, not from inside it, "
            "e.g.  docker pull ghcr.io/delivstat/swarmkit:latest"
        )
    else:
        _stderr(
            "Could not tell how swarmkit-runtime was installed here, so it will not run an "
            "installer that might target the wrong environment. Upgrade it yourself with:\n  "
            f"{up.manual_command(extras, to)}"
        )
    raise typer.Exit(_EXIT_USAGE)


@app.command()
def upgrade(
    check: Annotated[
        bool, typer.Option("--check", help="Show the plan and exit 1 if behind; never install.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not prompt before installing.")
    ] = False,
    to: Annotated[
        str | None,
        typer.Option("--to", help="Upgrade to this exact version instead of the newest."),
    ] = None,
) -> None:
    """Upgrade swarmkit-runtime in place, keeping its extras, after showing any breaking changes."""
    from swarmkit_runtime import _upgrade as up  # noqa: PLC0415
    from swarmkit_runtime._versions import runtime_version  # noqa: PLC0415

    installed = runtime_version()
    if not installed:
        _stderr("swarmkit-runtime is not installed as a package here; nothing to upgrade.")
        raise typer.Exit(_EXIT_USAGE)

    method, extras = up.detect_method(), up.detect_extras()
    if to:
        target = to
    else:
        try:
            target = up.latest_version()
        except up.UpgradeError as exc:
            _stderr(f"error: {exc}")
            raise typer.Exit(_EXIT_USAGE) from exc

    if up.version_key(target) <= up.version_key(installed):
        typer.echo(f"swarmkit-runtime {installed} is already the newest ({target}). Nothing to do.")
        raise typer.Exit(0)

    breaks = _print_plan(installed, target, method, extras)
    if check:
        raise typer.Exit(1)  # behind — CI can gate on it; the plan is already shown

    command = up.upgrade_command(method, extras, to)
    if command is None:
        _refuse_undriven(method, extras, to)

    if not yes:
        prompt = (
            "Proceed across the breaking change(s) above?"
            if breaks
            else "Proceed with the upgrade?"
        )
        # A breaking upgrade defaults to No — it must be a deliberate yes.
        if not typer.confirm(prompt, default=not breaks):
            typer.echo("Nothing done.")
            raise typer.Exit(0)

    assert command is not None  # _refuse_undriven exits otherwise
    typer.echo(f"\n$ {' '.join(command)}")
    code = up.run_command(command)
    if code != 0:
        _stderr(f"the upgrade command exited {code}")
    raise typer.Exit(code)
