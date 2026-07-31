"""Every documented CLI command is actually registered.

`swarmkit orchestrator` vanished in 1.127.0 and shipped broken through 1.127.1: an edit inserted a
helper function BETWEEN `@app.command()` and `def orchestrator`, so the decorator registered the
helper and the command lost its registration. Nothing failed — not lint, not mypy, not 1630 tests —
because no test ever asked the CLI what it exposes. The only symptom was
`No such command 'orchestrator'` at a user's terminal.

Two guards: the commands exist, and no private helper leaked into the command list (the other half
of a detached decorator, and the tell that one has happened).
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.cli import app

# Commands the docs promise (docs/site/reference/cli.md) plus the pipeline pair. Add to this list
# when adding a command — that is the point.
EXPECTED = [
    "ask",
    "chat",
    "checkpoints",
    "connect",
    "conversations",
    "debug",
    "edit",
    "eval",
    "gaps",
    "gates",
    "init",
    "knowledge-pack",
    "knowledge-server",
    "logs",
    "mcp-serve",
    "orchestrator",
    "run",
    "serve",
    "status",
    "stop",
    "trace",
    "validate",
    "why",
]

# Sub-apps registered with add_typer.
EXPECTED_GROUPS = ["author", "auth", "review", "pipeline", "memory", "trust", "fleet"]


def _command_names() -> set[str]:
    return {
        (cmd.name or cmd.callback.__name__.replace("_", "-"))
        for cmd in app.registered_commands
        if cmd.callback is not None
    }


def _group_names() -> set[str]:
    return {g.name or "" for g in app.registered_groups}


@pytest.mark.parametrize("name", EXPECTED)
def test_command_is_registered(name: str) -> None:
    assert name in _command_names(), (
        f"`swarmkit {name}` is not registered. A helper inserted between @app.command() and its "
        f"function silently steals the decorator — check the definition directly above it."
    )


@pytest.mark.parametrize("group", EXPECTED_GROUPS)
def test_command_group_is_registered(group: str) -> None:
    assert group in _group_names(), f"`swarmkit {group} ...` is not registered"


def test_no_private_helper_leaked_into_the_command_list() -> None:
    """The other half of a detached decorator: the helper below it becomes a command.

    A name starting with `_` is never a real command, so its presence means a decorator landed on
    the wrong function — which also means the intended command is missing.
    """
    leaked = sorted(n for n in _command_names() if n.startswith(("_", "-")))
    assert not leaked, f"private helpers registered as CLI commands: {leaked}"


def test_orchestrator_takes_the_flags_the_docs_document() -> None:
    """The reported invocation: `swarmkit orchestrator <ws> --serve-url … --database-url …`."""
    cmd = next(
        c
        for c in app.registered_commands
        if (c.name or (c.callback.__name__ if c.callback else "")) == "orchestrator"
    )
    assert cmd.callback is not None
    code = cmd.callback.__code__
    params = set(code.co_varnames[: code.co_argcount])
    assert {"workspace", "serve_url", "database_url"} <= params
