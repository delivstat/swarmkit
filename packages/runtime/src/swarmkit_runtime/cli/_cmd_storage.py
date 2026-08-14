"""``swarmkit storage`` — see where a workspace's data actually lives, and move it.

Two commands, both answering questions that had no answer before
(design/details/storage-service.md):

* ``status`` — the resolution report. A workspace whose ``storage:`` block was being ignored looked
  exactly like a workspace with no history: serve was empty, and nothing anywhere said the rows
  were in a file on one laptop.
* ``migrate`` — copy the local SQLite content into the configured Postgres. Without it, "switch to
  Postgres" means "abandon everything recorded so far", which is not a migration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ._app import app, storage_app

#: Tables copied by `migrate`, in FK-safe order. Explicit rather than reflected: an unknown table
#: in the source is something to report, not something to silently copy into production.
_TABLES: dict[str, tuple[str, ...]] = {
    "store.sqlite": (
        "jobs",
        "conversations",
        "conversation_messages",
        "run_usage",
        "serve_access",
        "pipeline_saga",
        "pipeline_events",
        "pipeline_artifacts",
        "governed_memory",
        "governed_memory_change_log",
    ),
    "audit.sqlite": ("audit_events",),
    "fleet.sqlite": ("fleet_memberships", "enrollment_tokens"),
}


@storage_app.command("status")
def storage_status(
    workspace: Annotated[
        Path, typer.Argument(help="Workspace root (directory with workspace.yaml).")
    ] = Path("."),
) -> None:
    """Show which backend each store resolves to, and where that decision came from."""
    from swarmkit_runtime.persistence import (  # noqa: PLC0415
        StorageConfigError,
        storage_for_workspace,
    )

    root = workspace.resolve()
    try:
        service = storage_for_workspace(root)
        lines = service.report()
    except StorageConfigError as exc:
        typer.echo(f"storage config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"storage for {root}:")
    typer.echo("")
    typer.echo(f"  {'store':<12} {'backend':<9} {'location'}  (source)")
    for line in lines:
        typer.echo(line)

    warnings = service.split_warnings()
    if warnings:
        typer.echo("")
        for line in warnings:
            typer.echo(f"  ! {line}")
        typer.echo("")
        typer.echo("  Copy the local rows over with:  swarmkit storage migrate <workspace>")


@storage_app.command("migrate")
def storage_migrate(
    workspace: Annotated[
        Path, typer.Argument(help="Workspace root (directory with workspace.yaml).")
    ] = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would be copied, write nothing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Copy this workspace's local SQLite rows into its configured Postgres store.

    Reads the SQLite files under ``.swarmkit/`` and inserts their rows into the databases the
    ``storage:`` config names. Additive and idempotent: rows that are already there are skipped on
    primary key, so re-running after a partial failure resumes rather than duplicates. Nothing is
    deleted — the SQLite files stay exactly as they are, which is the only sane default for a
    one-way copy you might want to repeat.
    """
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415
    from swarmkit_runtime.persistence._store import redacted_url  # noqa: PLC0415

    root = workspace.resolve()
    service = storage_for_workspace(root)
    counted, reason = _migration_plan(service, root)
    if reason:
        typer.echo(reason)
        raise typer.Exit(code=0)

    total = sum(rows for *_, rows in counted)
    typer.echo(f"Migrating {total} row(s) from {root / '.swarmkit'}:")
    for source, table, url, rows in counted:
        typer.echo(f"  {source.name:<14} {table:<28} {rows:>7} -> {redacted_url(url)}")

    if dry_run:
        typer.echo("\n--dry-run: nothing written.")
        raise typer.Exit(code=0)
    if not yes:
        typer.confirm("\nCopy these rows into the configured store?", abort=True)

    copied, skipped = _run_migration(service, counted)
    typer.echo("")
    typer.echo(f"Done: {copied} row(s) copied, {skipped} already present.")
    typer.echo("The SQLite files are unchanged. Verify with:  swarmkit storage status")
    typer.echo("Then archive them — leaving them in place is how a split brain starts.")


def _migration_plan(
    service: Any, root: Path
) -> tuple[list[tuple[Path, str, str, int]], str | None]:
    """What would be copied, plus a human reason when the answer is "nothing"."""
    from swarmkit_runtime.persistence import StoreKind  # noqa: PLC0415

    if not any(service.target(k).backend == "postgres" for k in StoreKind):
        return [], (
            "Nothing to migrate: every store already resolves to local SQLite.\n"
            "Configure storage.runtime.backend: postgres (+ url) first, then re-run."
        )

    counted: list[tuple[Path, str, str, int]] = []
    for filename, tables in _TABLES.items():
        source = root / ".swarmkit" / filename
        if not source.is_file():
            continue
        target = service.target(_KIND_FOR_FILE[filename])
        if target.backend != "postgres":
            continue
        counted.extend(
            (source, table, target.url, rows)
            for table in tables
            if (rows := _sqlite_count(source, table))
        )

    if not counted:
        return [], "Nothing to migrate: no local SQLite rows belong to a remote store."
    return counted, None


def _run_migration(service: Any, counted: list[tuple[Path, str, str, int]]) -> tuple[int, int]:
    """Copy each planned table, then re-sync the sequences. Returns (copied, already-present)."""
    from swarmkit_runtime.persistence._store import make_engine  # noqa: PLC0415

    # Create the destination schema first: the tables are defined by the modules that own them, and
    # building each store once is what creates them.
    _ensure_schema(service)

    copied = skipped = 0
    engines: list[Any] = []
    for source, table, _url, _rows in counted:
        dest_engine = service.engine(_KIND_FOR_FILE[source.name])
        if not any(e is dest_engine for e in engines):
            engines.append(dest_engine)
        src_engine = make_engine(f"sqlite:///{source}")
        try:
            dest_table = _reflect(dest_engine, table)
            if dest_table is None:
                typer.echo(f"  ! {table}: not present in the destination — skipped", err=True)
                continue
            n_copied, n_skipped = _copy_table(src_engine, dest_engine, dest_table, table)
        finally:
            src_engine.dispose()
        copied += n_copied
        skipped += n_skipped
        typer.echo(f"  {table:<28} copied {n_copied:>7}   already present {n_skipped:>7}")

    # Rows were inserted WITH their original ids, which leaves every owning sequence exactly where
    # it started. The next insert then reuses an id that is already there and dies on the primary
    # key — `pipeline_events` first, so no pipeline can be started at all. Reads all work, so the
    # store looks migrated right up until the first write. A migration that leaves the destination
    # unwritable has not succeeded, whatever its row counts say.
    for engine in engines:
        _resync_sequences(engine)
    return copied, skipped


def _owned_sequences(engine: Any) -> list[tuple[str, str, str]]:
    """Every sequence owned by a column, as (sequence, table, column).

    Read from ``pg_depend`` rather than a hard-coded list so a table added later is covered without
    anyone remembering to update this file — the failure mode being avoided is silent and only
    shows up on someone's next write.

    Scoped to ``current_schema()``. A Postgres instance is routinely shared, and SwarmKit's tables
    are only ever one schema of it; re-syncing sequences that belong to somebody else's application
    is not this command's business.
    """
    from sqlalchemy import text  # noqa: PLC0415

    with engine.connect() as conn:
        return [
            (row[0], row[1], row[2])
            for row in conn.execute(
                text("""
                SELECT n.nspname || '.' || s.relname, t.relname, a.attname
                FROM pg_class s
                JOIN pg_namespace n ON n.oid = s.relnamespace
                JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
                JOIN pg_class t ON t.oid = d.refobjid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                WHERE s.relkind = 'S' AND d.deptype IN ('a', 'i')
                  AND n.nspname = current_schema()
                """)
            )
        ]


def _resync_sequences(engine: Any) -> list[tuple[str, int]]:
    """Advance every owned sequence past its column's current maximum.

    Returns the (table, max) pairs that were actually moved. Raises when a sequence is still behind
    afterwards: reporting success over a store that cannot accept an insert is the whole bug.
    """
    from sqlalchemy import text  # noqa: PLC0415

    if engine.dialect.name != "postgresql":
        return []

    moved: list[tuple[str, int]] = []
    stale: list[str] = []
    for sequence, table, column in _owned_sequences(engine):
        with engine.begin() as conn:
            current = conn.execute(text(f'SELECT max("{column}") FROM "{table}"')).scalar()
            if current is None:
                # An empty table must keep its sequence at "next value is 1". setval(seq, 1, true)
                # would silently burn id 1 — a smaller bug than the one being fixed, but the same
                # kind, so it is not worth trading one for the other.
                conn.execute(text("SELECT setval(:seq, 1, false)"), {"seq": sequence})
                continue
            before = conn.execute(text("SELECT last_value FROM " + sequence)).scalar()
            conn.execute(
                text("SELECT setval(:seq, :val, true)"), {"seq": sequence, "val": int(current)}
            )
            after = conn.execute(text("SELECT last_value FROM " + sequence)).scalar()
            if int(after or 0) < int(current):
                stale.append(f"{table}.{column}")
            elif int(before or 0) < int(current):
                moved.append((table, int(current)))
                typer.echo(f"  {table + '.' + column:<28} sequence advanced to {current:>7}")

    if stale:
        raise typer.BadParameter(
            "sequences are still behind their table after re-sync: "
            + ", ".join(stale)
            + " — the store would reject the next insert, so the migration is not complete."
        )
    return moved


def _sqlite_count(path: Path, table: str) -> int:
    """Row count, or 0 when the table does not exist in that file."""
    import sqlite3  # noqa: PLC0415

    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if cur.fetchone() is None:
            return 0
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _ensure_schema(service: Any) -> None:
    """Create every destination table by building each store once — the constructors do it."""
    from swarmkit_runtime.persistence import StoreKind  # noqa: PLC0415

    service.store()
    service.membership_store()
    if service.target(StoreKind.AUDIT).backend == "postgres":
        service.audit_provider()
    service.memory_store()


def _reflect(engine: Any, table: str) -> Any:
    from sqlalchemy import MetaData, Table  # noqa: PLC0415

    meta = MetaData()
    try:
        return Table(table, meta, autoload_with=engine)
    except Exception:  # table absent in the destination — reported by the caller
        return None


def _copy_table(src_engine: Any, dest_engine: Any, dest_table: Any, table: str) -> tuple[int, int]:
    """Insert every source row that is not already present. Returns (copied, skipped)."""
    from sqlalchemy import func, select, text  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    columns = [c.name for c in dest_table.columns]
    with src_engine.connect() as src:
        rows = [dict(r._mapping) for r in src.execute(text(f"SELECT * FROM {table}"))]

    payload = [{k: v for k, v in row.items() if k in columns} for row in rows]
    if not payload:
        return 0, 0

    # ON CONFLICT DO NOTHING is what makes a re-run safe after a partial failure. Without it the
    # second attempt dies on the first already-copied row and the operator is left guessing how far
    # the first one got.
    stmt = pg_insert(dest_table).on_conflict_do_nothing()
    # Count either side of the insert instead of trusting `rowcount`: psycopg reports -1 for an
    # executemany, so a migration that copied every row reported "0 copied, N already present" —
    # indistinguishable from a re-run that did nothing.
    with dest_engine.begin() as dest:
        before = dest.execute(select(func.count()).select_from(dest_table)).scalar() or 0
        dest.execute(stmt, payload)
        after = dest.execute(select(func.count()).select_from(dest_table)).scalar() or 0
    copied = max(0, int(after) - int(before))
    return copied, len(payload) - copied


def _kinds_for_files() -> dict[str, Any]:
    from swarmkit_runtime.persistence import StoreKind  # noqa: PLC0415

    return {
        "store.sqlite": StoreKind.RUNTIME,
        "audit.sqlite": StoreKind.AUDIT,
        "fleet.sqlite": StoreKind.FLEET,
    }


class _KindLookup(dict[str, Any]):
    """Lazy so importing the CLI does not import persistence."""

    def __missing__(self, key: str) -> Any:
        self.update(_kinds_for_files())
        return dict.__getitem__(self, key)


_KIND_FOR_FILE = _KindLookup()


@app.command("system")
def system_info(
    workspace: Annotated[
        Path, typer.Argument(help="Workspace root (directory with workspace.yaml).")
    ] = Path("."),
    all_vars: Annotated[
        bool, typer.Option("--all", help="Include environment variables that are not set.")
    ] = False,
) -> None:
    """Versions, storage resolution, workspace properties and environment — what this instance is.

    The CLI half of the web UI's System page, and the same masking rules: a value declared under
    `secrets:` in workspace.env.yaml prints as "set", and connection URLs print with the password
    removed. This output lands in terminal scrollback and CI capture, so that is not optional.
    """
    from swarmkit_runtime._env_registry import (  # noqa: PLC0415
        environment_report,
        workspace_properties,
    )
    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    root = workspace.resolve()
    typer.echo(f"workspace: {root}")
    typer.echo(f"runtime:   {_pkg_version('swarmkit-runtime')}")
    typer.echo(f"web ui:    {_pkg_version('swarmkit-webui')}")

    typer.echo("\nstorage:")
    typer.echo(f"  {'store':<12} {'backend':<9} {'location'}  (source)")
    for line in storage_for_workspace(root).report():
        typer.echo(line)

    properties = workspace_properties(root)
    typer.echo("\nworkspace properties (workspace.env.yaml):")
    if not properties:
        typer.echo("  (none — no workspace.env.yaml in this workspace)")
    for prop in properties:
        typer.echo(f"  {prop['name']!s:<36} {prop['value']}")

    typer.echo("\nenvironment:")
    shown = 0
    for row in environment_report(include_unset=all_vars):
        if not row["set"] and not all_vars:
            continue
        shown += 1
        typer.echo(f"  {row['name']!s:<40} {row['value']}")
    if not shown:
        typer.echo("  (none of the known variables are set)")
    if not all_vars:
        typer.echo("\n  --all also lists the variables that are not set, with what each does.")


def _pkg_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"
