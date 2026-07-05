"""CLI commands — knowledge-server / docs-reader launchers + packaging stubs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    pass

import typer

from ._app import app
from ._common import (
    _not_implemented,
)

# ---- knowledge-server ----------------------------------------------------


@app.command(name="knowledge-server")
def knowledge_server(
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            help="Override the repo root (default: auto-detected).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Launch the SwarmKit Knowledge MCP Server (stdio)."""
    from swarmkit_runtime.knowledge._server import run_server  # noqa: PLC0415

    run_server(repo_root=repo.resolve() if repo else None)


# ---- docs-reader ---------------------------------------------------------


@app.command(name="docs-reader")
def docs_reader(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace root for resolving relative paths.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Launch the Document Reader MCP Server (stdio).

    Reads PDF, DOCX, Excel, CSV, draw.io, SVG, and text files.
    Document parsing libraries are optional — install as needed.
    """
    from swarmkit_runtime.docs_reader._server import run_server  # noqa: PLC0415

    run_server(workspace=workspace.resolve() if workspace else None)


@app.command()
def install(
    package: Annotated[
        str,
        typer.Argument(help="Package to install (path, .tar.gz, or URL)."),
    ],
    upgrade: Annotated[
        bool,
        typer.Option("--upgrade", "-U", help="Upgrade if already installed."),
    ] = False,
) -> None:
    """Install a SwarmKit expertise package."""
    from swarmkit_runtime.packages._installer import install_package  # noqa: PLC0415

    install_package(package, upgrade=upgrade)


@app.command()
def packages(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root (or global packages dir)."),
    ] = Path("."),
) -> None:
    """List installed SwarmKit expertise packages."""
    from swarmkit_runtime.packages._installer import list_packages  # noqa: PLC0415

    list_packages(workspace_path)


@app.command()
def publish(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace to package and publish."),
    ] = Path("."),
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output directory for the package tarball."),
    ] = Path("./dist"),
) -> None:
    """Package a workspace for distribution."""
    from swarmkit_runtime.packages._publisher import publish_package  # noqa: PLC0415

    publish_package(workspace_path.resolve(), output.resolve())


@app.command()
def eject(topology: str, output: str = "./generated/") -> None:
    """Export the LangGraph code the runtime would execute (design §14.4)."""
    _not_implemented("eject", milestone="M9 (eject)")
