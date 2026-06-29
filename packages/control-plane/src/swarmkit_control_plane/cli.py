"""CLI for the SwarmKit control plane."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="SwarmKit control plane — fleet panel API + instance registry.")

_CORS_ENV = "SWARMKIT_CONTROL_PLANE_CORS_ORIGINS"
_OPERATOR_TOKENS_ENV = "SWARMKIT_CONTROL_PLANE_OPERATOR_TOKENS"


@app.command()
def serve(
    data_dir: Annotated[
        Path, typer.Option("--data-dir", help="Where to store the registry sqlite.")
    ] = Path(".swarmkit-control-plane"),
    host: Annotated[str, typer.Option("--host", help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port.")] = 8800,
    cors_origin: Annotated[
        list[str] | None,
        typer.Option(
            "--cors-origin",
            help=(
                "Browser origin allowed to call the panel (repeatable). CORS is config-only — "
                "pass the fleet UI's origin (e.g. its dev URL). Also read from "
                f"${_CORS_ENV} (comma-separated)."
            ),
        ),
    ] = None,
    operator_token: Annotated[
        list[str] | None,
        typer.Option(
            "--operator-token",
            help=(
                "Operator bearer token granting full panel access (repeatable). When set, the "
                "panel requires auth on every route except /health; otherwise it runs open. Also "
                f"read from ${_OPERATOR_TOKENS_ENV} (comma-separated)."
            ),
        ),
    ] = None,
) -> None:
    """Start the control-plane API."""
    import uvicorn  # noqa: PLC0415

    from swarmkit_control_plane._app import create_app  # noqa: PLC0415
    from swarmkit_control_plane._registry import SqliteRegistry  # noqa: PLC0415

    origins = list(cors_origin or [])
    origins += [o.strip() for o in os.environ.get(_CORS_ENV, "").split(",") if o.strip()]

    op_tokens = list(operator_token or [])
    op_tokens += [
        t.strip() for t in os.environ.get(_OPERATOR_TOKENS_ENV, "").split(",") if t.strip()
    ]

    registry = SqliteRegistry(data_dir / "registry.sqlite")
    if not op_tokens:
        typer.echo(
            "warning: no operator tokens set — panel API is UNAUTHENTICATED. "
            f"Set --operator-token or ${_OPERATOR_TOKENS_ENV} to require auth.",
            err=True,
        )
    uvicorn.run(
        create_app(registry, cors_origins=origins, operator_tokens=op_tokens),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    app()
