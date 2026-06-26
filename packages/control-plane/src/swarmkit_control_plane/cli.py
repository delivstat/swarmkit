"""CLI for the SwarmKit control plane."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="SwarmKit control plane — fleet panel API + instance registry.")


@app.command()
def serve(
    data_dir: Annotated[
        Path, typer.Option("--data-dir", help="Where to store the registry sqlite.")
    ] = Path(".swarmkit-control-plane"),
    host: Annotated[str, typer.Option("--host", help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port.")] = 8800,
) -> None:
    """Start the control-plane API."""
    import uvicorn  # noqa: PLC0415

    from swarmkit_control_plane._app import create_app  # noqa: PLC0415
    from swarmkit_control_plane._registry import SqliteRegistry  # noqa: PLC0415

    registry = SqliteRegistry(data_dir / "registry.sqlite")
    uvicorn.run(create_app(registry), host=host, port=port)


if __name__ == "__main__":
    app()
