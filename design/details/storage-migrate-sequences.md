# `storage migrate` must leave the destination writable

Status: implemented (runtime 1.136.0). Amends the migration described in the Postgres backend work.

## The failure

`swarmkit storage migrate` copies rows into Postgres **with their original primary keys** and never
advances the owning sequences. A sequence sits at 1 while `max(id)` is 14, and the next insert
reuses a live id:

```
UniqueViolation: duplicate key value violates unique constraint "pipeline_events_pkey"
DETAIL: Key (id)=(1) already exists.
```

`pipeline_events` is the blocking table — every `pipeline emit` writes there — so after a migration
**no pipeline could be started at all**.

Measured immediately after a real migration:

| table | sequence | max(id) | state |
| --- | ---: | ---: | --- |
| pipeline_events | 2 | 14 | STALE |
| serve_access | 23 | 83 | STALE |
| run_usage | 21 | 21 | ok |
| governed_memory_change_log | 5 | 5 | ok |

The rows that read "ok" are only ok because nothing had been inserted since.

## Why it survived

The migration reported success and printed row counts. Every **read** worked — runs displayed
correctly in the UI and in `pipeline sagas`. The failure appeared later, on the next **write**, as a
`UniqueViolation` naming a constraint rather than the migration. And `swarmkit serve` kept advising:

> configured for postgres, but … still holds ~162 rows, move them with: swarmkit storage migrate

— inviting operators to run the very command that left the store unable to accept a pipeline event.

## Goal

A migration reports success only if the destination can accept the next insert.

## Non-goals

- Changing the copy strategy. Preserving source ids is deliberate: it is what makes the migration
  idempotent on re-run and keeps foreign keys and existing artifact refs valid.
- Migrating anything back out of Postgres.

## Design

After copying, advance every sequence owned by a column past that column's current maximum.

**Enumerated from `pg_depend`, not a hard-coded table list.** A table added later is covered without
anyone remembering to update the migration — and "remembering" is exactly what fails, silently, in
this bug class.

**Scoped to `current_schema()`.** A Postgres instance is routinely shared and SwarmKit's tables are
one schema of it. A migration that fixed its own store by quietly moving another application's
sequences would be a worse bug than the one it fixes.

**Empty tables keep `nextval = 1`.** The obvious `setval(seq, COALESCE(max(id), 1), true)` burns id
1 on an empty table. Smaller than the bug being fixed, and the same kind, so not worth trading.

**A sequence still behind after the re-sync fails the command.** This is the point of the whole
change: a migration that leaves the store unwritable must not report success, whatever its row
counts say.

## The second defect

Found while reproducing. `psycopg` returns `rowcount == -1` for an executemany, so:

```python
copied = int(result.rowcount) if result.rowcount and result.rowcount > 0 else 0
```

...evaluated to 0 after copying all 14 rows, and the command announced `0 copied, 14 already
present` — byte-identical to a re-run that did nothing. An operator reading that would reasonably
conclude the migration had already happened. Counts are now taken either side of the insert.

## Test plan

`packages/runtime/tests/test_storage_migrate_sequences.py`, against a real Postgres
(`SWARMKIT_TEST_POSTGRES_URL`) — sequences do not exist in SQLite, so no mocked test could have
caught this:

- a migrated table accepts the next insert (the bug as the user meets it: not "is the data there"
  but "can I start a pipeline")
- without the re-sync the insert dies, pinning the exact `UniqueViolation`
- every owned sequence is discovered from `pg_depend`
- a neighbouring schema is left untouched
- re-running never drags a sequence back behind rows written since
- an empty table does not burn id 1
- a sequence that is still stale afterwards fails the command loudly
- SQLite destinations are a no-op, not an error (runs without a server, so CI covers it)

Each test also passes with the fix reverted only where it is meant to: 7 of 8 fail without it.

## Demo

Against a throwaway Postgres, before and after:

```
  pipeline_events              copied      14   already present       0
  pipeline_events.id           sequence advanced to      14
```
```
INSERT INTO pipeline_events (...) RETURNING id;   -- 15, where it previously raised
```
