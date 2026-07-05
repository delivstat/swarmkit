"""The Typer application objects — the root ``app`` plus the ``review`` / ``author`` / ``auth``
sub-command groups. Command modules import these and attach handlers via decorator; the package
``__init__`` imports those modules for their registration side effects."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="swarmkit",
    help="Compose, run, and grow multi-agent swarms.",
    no_args_is_help=True,
)

review_app = typer.Typer(help="Human-in-the-loop review queue.")
author_app = typer.Typer(help="Conversational authoring for topologies, skills, archetypes.")
auth_app = typer.Typer(help="Manage serve API auth (token minting).")

app.add_typer(review_app, name="review")
app.add_typer(author_app, name="author")
app.add_typer(auth_app, name="auth")
