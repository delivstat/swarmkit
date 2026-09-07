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


#: A `contradict` left the trusted memory untouched. Exiting 0 would tell a seeding script the
#: fact landed when it did not — the defect shape this codebase spends its time removing.
_EXIT_CONTRADICT = 3


def _warn_if_nothing_reads(workspace: Path) -> None:
    """Warn when no agent binds `memory-reader` at pre_input.

    Seeding before wiring is a legitimate order of work, so this does not refuse. But a store
    filling with facts that reach no run is the silent chain this whole feature exists to break:
    the store is None, or the agent lacks the grant, or the model never emits — and every one of
    those looks identical to "nothing to remember".
    """
    try:
        rt = WorkspaceRuntime.from_workspace_path(workspace)
        bindings = rt.reachability()
    except Exception:
        return
    declared = str(getattr(bindings, "unreached", "")) + str(rt._workspace.raw)
    if "memory-reader" not in declared:
        _stderr(
            "warning: no agent binds `memory-reader` at pre_input, so nothing will read this yet. "
            "The fact is stored; wire a reader to use it."
        )


def _report(outcome: object, key: str) -> int:
    """Print the op — never a bare 'added'. Returns the exit code."""
    op = str(getattr(outcome, "op", "?"))
    if op == "contradict":
        _stderr(f"contradict  {key} — NOT written; quarantined for review")
        _stderr("  the trusted memory is unchanged. Resolve with: swarmkit memory resolve <id>")
        return _EXIT_CONTRADICT
    typer.echo(f"{op:<10} {key}")
    return 0


@memory_app.command()
def add(
    subject: Annotated[str, typer.Argument(help="What the fact is about.")] = "",
    attribute: Annotated[str, typer.Argument(help="Which property of it.")] = "",
    value: Annotated[str, typer.Argument(help="The fact itself.")] = "",
    workspace: _PathArg = Path("."),
    memory_type: Annotated[
        str, typer.Option("--type", help="semantic | profile | procedural | episodic | working")
    ] = "semantic",
    confidence: Annotated[float, typer.Option("--confidence", min=0.0, max=1.0)] = 1.0,
    source: Annotated[
        str, typer.Option("--source", help="Who asserted this. Defaults to the OS user.")
    ] = "",
    from_file: Annotated[
        Path | None,
        typer.Option("--from-file", help="Bulk: {'memories': [...]}, the agent's own shape."),
    ] = None,
) -> None:
    """Write a fact into governed memory, through the same path an agent writes through.

    The point is not to insert a row: `write()` reconciles the candidate against current memory and
    reports `new` / `update` / `reinforce` / `refine` / `contradict`. A human add inherits all of
    it, contradiction handling included.
    """
    import getpass  # noqa: PLC0415

    from swarmkit_runtime.governed_memory import MemoryCandidate  # noqa: PLC0415

    if from_file is not None and (subject or attribute or value):
        _stderr("error: --from-file and positional arguments are mutually exclusive")
        raise typer.Exit(_EXIT_USAGE)
    if from_file is None and not (subject and attribute and value):
        _stderr("error: subject, attribute and value are all required (or use --from-file)")
        raise typer.Exit(_EXIT_USAGE)

    who = source or getpass.getuser()
    store = _store(workspace)
    _warn_if_nothing_reads(workspace)

    if from_file is not None:
        try:
            payload = json.loads(from_file.read_text())
            raw = payload["memories"]
            candidates = [
                MemoryCandidate(
                    subject=str(m["subject"]),
                    attribute=str(m["attribute"]),
                    value=str(m["value"]),
                    type=m.get("type", "semantic"),
                    confidence=float(m.get("confidence", 1.0)),
                    source=str(m.get("source") or who),
                )
                for m in raw
            ]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Parsed in full before writing anything: a malformed file must not leave half an
            # estate seeded, with no way to tell which half.
            _stderr(f"error: {from_file} is not a valid memories file: {exc}")
            raise typer.Exit(_EXIT_USAGE) from exc

        worst = 0
        counts: dict[str, int] = {}
        for candidate in candidates:
            outcome = store.write(candidate)
            counts[outcome.op] = counts.get(outcome.op, 0) + 1
            worst = max(worst, _report(outcome, f"{candidate.subject}/{candidate.attribute}"))
        typer.echo("  " + ", ".join(f"{n} {op}" for op, n in sorted(counts.items())))
        raise typer.Exit(worst)

    outcome = store.write(
        MemoryCandidate(
            subject=subject,
            attribute=attribute,
            value=value,
            type=memory_type,  # type: ignore[arg-type]
            confidence=confidence,
            source=who,
        )
    )
    raise typer.Exit(_report(outcome, f"{subject}/{attribute}"))


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
