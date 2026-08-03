# Reported bugs — the ledger

Bugs found by running SwarmKit against real work, not by CI. Each entry records what broke, why the
existing tests missed it, and where its regression test now lives. Add to it when a bug is reported;
do not delete entries when they are fixed — the value is in the pattern.

**Read the pattern section at the bottom before adding.** These are not unrelated bugs.

## Open

| Bug | Component | Note |
| --- | --- | --- |
| Decision skills never run on a harness executor, including `required: true` | `_compiler.py` | [harness-parity-gaps](harness-parity-gaps.md) #2 |
| `output_schema` ignored on the harness path | `_harness_node.py` | [harness-parity-gaps](harness-parity-gaps.md) #3 |
| `TaskSpec.context_files` set but never delivered | executor plumbing | [harness-parity-gaps](harness-parity-gaps.md) #4 |
| Images reach a model only via MCP `ImageContent`; relative paths resolve nowhere | sandbox + gateway | [harness-parity-gaps](harness-parity-gaps.md) #5 |
| `/jobs` shows only in-flight jobs; `/jobs/history` exists server-side, unused by the UI | web UI | — |

## Fixed

### `swarmkit storage migrate` left Postgres unwritable (1.136.0)

Rows were copied **with their original primary keys** and the owning sequences never advanced, so a
sequence sat at 1 while `max(id)` was 14. The next insert reused a live id:

```
UniqueViolation: duplicate key value violates unique constraint "pipeline_events_pkey"
DETAIL: Key (id)=(1) already exists.
```

`pipeline_events` is the blocking table — every `pipeline emit` writes there — so after a migration
**no pipeline could start at all**.

Why it survived: the migration reported success with row counts; every *read* worked, so runs
displayed correctly in the UI and in `pipeline sagas`; the failure appeared later on an unrelated
write, naming a constraint rather than the migration. And `swarmkit serve` kept advising operators
to run the very command that broke the store.

Fixed by re-syncing every sequence owned by a column, enumerated from `pg_depend` so tables added
later are covered without anyone remembering. The migration now **fails** if a sequence is still
behind afterwards — a migration that leaves the store unable to accept an insert must not report
success. Empty tables keep `nextval = 1` rather than burning id 1 to `COALESCE(max, 1)`.

Found while reproducing: `psycopg` returns `rowcount == -1` for an executemany, so a migration that
copied all 14 rows announced `0 copied, 14 already present` — byte-identical to a re-run that did
nothing. Counts are now taken either side of the insert.

Tests: `packages/runtime/tests/test_storage_migrate_sequences.py` (needs
`SWARMKIT_TEST_POSTGRES_URL`; sequences do not exist in SQLite, so this could not have been caught
by a mocked test).

### Harness tool outcomes discarded (1.135.0)

A design agent described UI screens it had never seen, three runs running, because the image tool
returned nothing and the trace rendered `view-screenshot ✓` either way. Three of four adapters
mapped no tool outcome at all; `ExecToolCall.status` had no shared vocabulary; and
`ToolCall.result_length` was given the *argument* length, so a tool that returned nothing showed a
healthy number because the path was long. See `design/details/harness-tool-outcomes.md`.

### Per-stage traces overwrote each other (1.133.0)

Every stage of a pipeline used `thread_id=correlation_id`, which is also the trace's `run_id` and
the file name — so a three-stage run left one trace, the last stage's. Tests:
`test_stage_run_id.py`.

### Earlier (1.123.0–1.132.0)

Storage config never read; a degraded checkpointer reporting success; `--mcp-config` swallowing the
server list as filenames; `str(engine.url)` masking the password it was asked to print;
`swarmkit orchestrator` unregistered by a decorator separated from its function. Tests:
`test_reported_bugs.py`, `test_store_factory.py`, `test_cli_command_registration.py`,
`test_engine_url_password.py`.

## The pattern

Nearly every entry above is the same bug wearing different clothes:

> **Information exists, nothing surfaces it, and absence renders as success.**

The sequence was knowable. The tool's failure was in the stream. The checkpointer knew it had
degraded. The trace knew it was overwriting a file. In each case something computed the truth and
then dropped it, and the layer above printed a confident status line over the gap.

Two working rules follow:

1. **A component that cannot do its job must say so louder than it says "done".** Prefer failing the
   operation to completing it in a degraded state — `storage migrate` now refuses to report success
   over a store it has left unwritable.
2. **"Unknown" is not "fine".** Where a status can be unreported, model it as a third value rather
   than folding it into the healthy one. That is why `ExecToolStatus` is `""` / `ok` / `error`.

And a testing rule, learned repeatedly: **these bugs are invisible to mocked tests.** The sequence
bug needed a real Postgres; the governance bug needed a real run. Unit tests are for coverage; a
live pipeline is for confidence.
