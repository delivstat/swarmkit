"""CLI for the SwarmKit control plane."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="SwarmKit control plane — fleet panel API + instance registry.")

_CORS_ENV = "SWARMKIT_CONTROL_PLANE_CORS_ORIGINS"


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
                "Extra browser origin allowed to call the panel (repeatable). Any localhost "
                f"origin is always allowed. Also read from ${_CORS_ENV} (comma-separated)."
            ),
        ),
    ] = None,
) -> None:
    """Start the control-plane API."""
    import uvicorn  # noqa: PLC0415

    from swarmkit_control_plane._app import create_app  # noqa: PLC0415
    from swarmkit_control_plane._registry import SqliteRegistry  # noqa: PLC0415

    origins = list(cors_origin or [])
    env_origins = os.environ.get(_CORS_ENV, "")
    origins += [o.strip() for o in env_origins.split(",") if o.strip()]

    registry = SqliteRegistry(data_dir / "registry.sqlite")
    uvicorn.run(create_app(registry, cors_origins=origins), host=host, port=port)


if __name__ == "__main__":
    app()
