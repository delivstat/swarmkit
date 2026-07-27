"""``swarmkit memory`` — the CLI half of the governed-memory surface (design/details/governed-
memory.md). Search, inspect a fact's history, and resolve quarantined contradictions over the same
``GovernedMemoryStore`` the serve ``/memory`` endpoints use — CLI ⇄ serve parity, one contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    change_to_dict,
    memory_to_dict,
    quarantine_to_dict,
)

from ._app import memory_app
from ._common import _EXIT_USAGE, _stderr

_PathArg = Annotated[
    Path, typer.Option("--workspace", "-w", help="Workspace root (directory with workspace.yaml).")
]


def _store(workspace: Path) -> GovernedMemoryStore:
    """The governed-memory store via the SAME service seam serve uses
    (``WorkspaceRuntime.governed_memory``) — CLI and serve share one construction path, not two."""
    store: GovernedMemoryStore | None = WorkspaceRuntime.from_workspace_path(
        workspace
    ).governed_memory
    if store is None:
        _stderr("error: this workspace declares no governed memory (add the governed-memory skill)")
        raise typer.Exit(_EXIT_USAGE)
    return store


@memory_app.command()
def search(
    query: Annotated[str, typer.Argument(help="Relevance query. Empty lists all, ranked.")] = "",
    workspace: _PathArg = Path("."),
    type_: Annotated[
        str | None, typer.Option("--type", help="Filter to a memory type.", show_default=False)
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max results.")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Search governed memory (relevance-ranked; empty query lists all by confidence)."""
    hits = _store(workspace).search(query, types=[type_] if type_ else None, limit=limit)
    if json_output:
        typer.echo(json.dumps([memory_to_dict(m) for m in hits], indent=2))
        return
    if not hits:
        typer.echo("no matching memories.")
        return
    for m in hits:
        typer.echo(f"  {m.subject} · {m.attribute} = {m.value}")
        typer.echo(
            f"      type={m.type} confidence={m.confidence:.2f} reinforced x{m.reinforce_count}"
        )


@memory_app.command()
def get(
    subject: Annotated[str, typer.Argument(help="Memory subject, e.g. user:alice.")],
    attribute: Annotated[str, typer.Argument(help="Memory attribute, e.g. preferred_language.")],
    workspace: _PathArg = Path("."),
    history: Annotated[
        bool, typer.Option("--history", help="Show the append-only change log.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show the current memory for a (subject, attribute) key, optionally with its full history."""
    store = _store(workspace)
    current = store.get(subject, attribute)
    log = store.history(subject, attribute) if history else []
    if json_output:
        payload = {
            "current": memory_to_dict(current) if current else None,
            "history": [change_to_dict(e) for e in log],
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    if current is None:
        typer.echo(f"no memory for {subject} · {attribute}")
        raise typer.Exit(1)
    typer.echo(f"  {current.subject} · {current.attribute} = {current.value}")
    typer.echo(f"      type={current.type} confidence={current.confidence:.2f}")
    for e in log:
        before = e.before["value"] if e.before else "∅"
        typer.echo(
            f"    {e.timestamp[:19]}  {e.op:<9} {before} → {e.after['value']}  ({e.decided_by})"
        )


@memory_app.command()
def quarantine(
    workspace: _PathArg = Path("."),
    status: Annotated[
        str, typer.Option("--status", help="pending | accepted | rejected.")
    ] = "pending",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List quarantined contradictions awaiting (or resolved by) a curator."""
    items = _store(workspace).list_quarantine(status=status)
    if json_output:
        typer.echo(json.dumps([quarantine_to_dict(q) for q in items], indent=2))
        return
    if not items:
        typer.echo(f"no {status} quarantine items.")
        return
    for q in items:
        typer.echo(f"  #{q.id}  {q.memory_key}")
        typer.echo(f"      proposed: {q.candidate.get('value')!r}  vs current: {q.current_value!r}")
        typer.echo(f"      {q.reasoning}")


@memory_app.command()
def resolve(
    quarantine_id: Annotated[
        int, typer.Argument(help="Quarantine item id (from `memory quarantine`).")
    ],
    by: Annotated[str, typer.Option("--by", help="Resolver identity (the curator).")],
    accept: Annotated[
        bool,
        typer.Option(
            "--accept/--reject",
            help="Accept applies the proposal as an update; reject discards it.",
        ),
    ],
    workspace: _PathArg = Path("."),
) -> None:
    """Resolve a quarantined contradiction — the one hard human gate in the memory path (§8)."""
    outcome = _store(workspace).resolve_quarantine(quarantine_id, accept=accept, resolved_by=by)
    if accept and outcome is None:
        _stderr(f"error: no pending quarantine item #{quarantine_id}")
        raise typer.Exit(_EXIT_USAGE)
    if accept:
        assert outcome is not None
        typer.echo(f"accepted #{quarantine_id} → {outcome.op}: {outcome.memory.value}")
    else:
        typer.echo(f"rejected #{quarantine_id} (trusted value stands)")
