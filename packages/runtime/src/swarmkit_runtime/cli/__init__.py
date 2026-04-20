"""SwarmKit CLI — entry points for authoring and execution (design §14.2).

Implementation stub. The `app` object is the Typer application wired to
`swarmkit` in `pyproject.toml` [project.scripts].
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="swarmkit",
    help="Compose, run, and grow multi-agent swarms.",
    no_args_is_help=True,
)


@app.command()
def init() -> None:
    """Launch the Workspace Authoring Swarm in terminal chat mode (design §14.2)."""
    raise NotImplementedError("Workspace Authoring Swarm wiring pending — see design §11.")


author_app = typer.Typer(help="Conversational authoring for topologies, skills, archetypes.")
app.add_typer(author_app, name="author")


@author_app.command("topology")
def author_topology(name: str | None = typer.Argument(None)) -> None:
    """Launch the Topology Authoring Swarm variant (design §14.2)."""
    raise NotImplementedError


@author_app.command("skill")
def author_skill(name: str | None = typer.Argument(None)) -> None:
    """Launch the Skill Authoring Swarm (design §12)."""
    raise NotImplementedError


@author_app.command("archetype")
def author_archetype(name: str | None = typer.Argument(None)) -> None:
    """Launch the Archetype Authoring Swarm variant (design §14.2)."""
    raise NotImplementedError


@app.command()
def run(topology: str, input: str | None = None) -> None:
    """One-shot execution of a topology (design §14.1)."""
    raise NotImplementedError


@app.command()
def serve(path: str, port: int = 8000) -> None:
    """Persistent / scheduled mode (design §14.1)."""
    raise NotImplementedError


@app.command()
def eject(topology: str, output: str = "./generated/") -> None:
    """Export the LangGraph code the runtime would execute (design §14.4)."""
    raise NotImplementedError


if __name__ == "__main__":
    app()
