"""``swarmkit providers`` — inspect declarative model providers.

A provider is a YAML over a wire-format family (``design/details/declarative-model-providers.md``).
``list`` shows every declared provider, where it came from, and whether it is READY — the one
thing an operator asks when ``provider: groq`` is not registering: is the key set. ``show``
prints the resolved provider, chain and all, which is what a reviewer reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime.model_providers import ProviderSpecError, load_provider_specs, resolve_chain
from swarmkit_runtime.model_providers._declarative import _load_dir

from ._app import providers_app


@providers_app.command("list")
def providers_list(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """List every declared provider, its family, its source, and whether it is ready."""
    root = workspace_path.resolve()
    try:
        specs = load_provider_specs(root)
    except ProviderSpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    workspace_ids = set(_load_dir(root / "providers"))
    if not specs:
        typer.echo("No providers found.")
        return
    for pid in sorted(specs):
        try:
            resolved = resolve_chain(pid, specs)
        except ProviderSpecError as exc:
            typer.echo(f"  {pid:<24} INVALID    {exc}")
            continue
        source = "workspace" if pid in workspace_ids else "bundled"
        if resolved.auth.api_key_env is None:
            status = "ready (no auth)"
        elif resolved.ready:
            status = f"ready ({resolved.auth.api_key_env} set)"
        else:
            status = f"needs {resolved.auth.api_key_env}"
        typer.echo(f"  {pid:<24} {resolved.family:<18} {source:<10} {status}")


@providers_app.command("show")
def providers_show(
    provider_id: Annotated[str, typer.Argument(help="Provider id.")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Show a provider resolved through its chain — what actually reaches the family."""
    root = workspace_path.resolve()
    try:
        specs = load_provider_specs(root)
        if provider_id not in specs:
            typer.echo(f"Unknown provider: {provider_id!r}")
            raise typer.Exit(code=1)
        r = resolve_chain(provider_id, specs)
    except ProviderSpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"id:           {r.id}")
    typer.echo(f"chain:        {' -> '.join(r.chain)}")
    typer.echo(f"source:       {specs[provider_id].source}")
    typer.echo(f"base_url:     {r.base_url or '(family default)'}")
    if r.auth.api_key_env is None:
        typer.echo("auth:         none")
    else:
        typer.echo(
            f"auth:         {r.auth.api_key_env} -> {r.auth.header}: {r.auth.scheme} <key>"
            f"  [{'set' if r.ready else 'NOT SET'}]"
        )
    models = "any" if r.accept_any_model else (r.model_pattern or "(none)")
    typer.echo(f"models:       {models}")
    caps = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in r.capabilities.items())
    typer.echo(f"capabilities: {caps}")
    if r.lift_to_root:
        typer.echo(f"lift_to_root: {', '.join(r.lift_to_root)}")
    if r.headers:
        typer.echo(f"headers:      {dict(r.headers)}")
    if r.extra_body:
        typer.echo(f"extra_body:   {dict(r.extra_body)}")
