---
title: One storage service — every store resolved in one place
description: Six components each construct their own store, three of them from hardcoded sqlite paths, and the schema validates backends nothing reads. The result is a workspace configured for Postgres whose sagas, audit trail and governed memory all sit in a local file, with no error at any point. This replaces every construction site with a single StorageService that resolves once, owns the engines, and reports what it chose.
tags: [runtime, persistence, storage, cli, serve, governance]
status: draft
---

# One storage service

**Scope:** `runtime` (`persistence/`, `audit/`, `governed_memory/`, `orchestration/`, `artifacts/`,
`fleet/`, `_workspace_runtime`, serve, CLI), `schema` (the `storage` block)
**Design reference:** §14 (runtime architecture), §8.3 (append-only audit). Supersedes the
per-caller resolution introduced piecemeal since `postgres-backend.md`.
**Status:** draft

## Goal

One service that answers "which store do I use, and how do I open it" for every component, so a
workspace's `storage:` block means the same thing everywhere and cannot be bypassed.

## Non-goals

- **Not a new backend.** SQLite and Postgres, as today. What changes is *who decides*.
- **Not an ORM or a repository layer.** Components keep their own table modules and queries; the
  service hands out a resolved engine, not a data-access API.
- **Not changing the fleet store's separation.** Enrollment credentials stay in their own database
  by design; that separation becomes explicit and configured rather than hardcoded.
- **Not automatic data migration.** Moving existing rows is a separate, opt-in command (see
  "Open questions").

## The problem: six resolutions, four of them wrong

| store | constructed at | honours `storage.runtime`? |
| --- | --- | --- |
| serve main store | `create_store(path, workspace.raw)` | **yes** |
| `swarmkit orchestrator` | `_cmd_orchestrator._resolve_saga_store_url` | **yes** |
| `swarmkit pipeline *` | `_cmd_pipeline.py:29` — hardcoded `sqlite:///…/store.sqlite` | no |
| audit provider | `audit/_store.py:165` — `_resolve_backend(root)`, no workspace config | no |
| governed memory | `governed_memory/_store.py:86` — hardcoded `sqlite:///…/store.sqlite` | no |
| checkpoints | `_workspace_runtime.py:355` — hardcoded `SqliteSaver` | no |

Observed on a workspace declaring `postgres` for all three storage blocks, with the URL confirmed
present in the environment: Postgres held twelve tables at **zero rows**, while the local
`store.sqlite` held the entire operational history — 11 sagas, 28 pipeline artifacts, 18 usage
records, and a governed-memory store with its reconciliation change-log. `audit_events` and
`governed_memory` did not exist in Postgres at all, because those two providers had never opened a
connection to it.

Three compounding defects, all silent:

1. **Hardcoded paths.** Three components never consult configuration at all.
2. **A resolver called without the config.** `audit/_store.py` passes no `workspace_raw`, so it can
   only see environment variables — and the environment path keys off `SWARMKIT_STORE_BACKEND`, so
   a `SWARMKIT_STORE_URL` beginning `postgresql://` is ignored on its own. A URL names its backend
   unambiguously; requiring a second variable to believe it is a trap.
3. **Schema promises nothing reads.** `storage.audit.backend` and `storage.checkpoints.backend`
   are enum-validated (`sqlite | postgres`, plus `agt` for audit) and read by nothing.
   `storage.checkpoints.backend: postgres` is not merely unread but *unimplementable* today —
   `langgraph.checkpoint.postgres` is not an installed dependency. `swarmkit validate` accepts all
   of it.

The severity ordering is not the obvious one. A saga in the wrong database is recoverable — re-run
the pipeline. **Governed memory is not**: it is the one store whose value is cumulative, and it was
writing to a file no other process opens. A learning loop whose output is unreachable has not
learned anything shareable.

## The service

```python
class StorageService:
    """The single source of truth for where data lives. Nothing else constructs a store."""

    @classmethod
    def for_workspace(cls, root: Path, workspace_raw: Any = None) -> StorageService: ...

    # One resolved target per kind, with its source recorded for the startup report.
    def target(self, kind: StoreKind) -> StoreTarget: ...      # (backend, url, source)

    # Engines are cached per URL: one pool per database, not one per component.
    def engine(self, kind: StoreKind) -> Engine: ...

    def store(self) -> Store: ...                    # jobs, conversations, usage
    def saga_store(self) -> SqlSagaStore: ...
    def artifact_store(self) -> ArtifactStore: ...
    def audit_provider(self) -> AuditProvider: ...
    def memory_store(self) -> GovernedMemoryStore: ...
    def checkpointer(self) -> Any: ...
    def membership_store(self) -> MembershipStore: ...

    def report(self) -> list[str]: ...               # one line per kind, for startup logging
```

`StoreKind` is an enum — `runtime | audit | checkpoints | artifacts | memory | saga | fleet` — so a
component asks for a *kind*, never a path.

### Resolution, once

Per kind, in order:

1. `SWARMKIT_STORE_BACKEND` / `SWARMKIT_STORE_URL` (or `DATABASE_URL`) — process-wide override.
2. `storage.<kind>` when that kind has its own block (`audit`, `checkpoints`).
3. `storage.runtime` — the workspace default for every SQL store.
4. SQLite under `{workspace}/.swarmkit/`.

**A URL implies its backend.** `postgresql://…` resolves to `postgres` without
`SWARMKIT_STORE_BACKEND`. Setting only a URL is the common case and currently the silent one.

**A declared backend that cannot be honoured raises**, consistent with the rule established in
1.127.0: refusing to start beats writing to a different database than the one configured. That
covers `checkpoints.backend: postgres` while `langgraph-checkpoint-postgres` is absent — with a
message naming the missing extra rather than a generic failure.

### Engines are shared

Today each component calls `make_engine` independently, so one workspace on Postgres opens a
connection pool per component. The service caches by resolved URL, so components sharing a target
share a pool. This is a side benefit, not the motivation — but it is the reason `engine(kind)` is
on the service rather than each component holding its own.

### It reports what it chose

Startup logs one line per kind:

```
storage: runtime=postgres (workspace.yaml)  audit=postgres (workspace.yaml)
         checkpoints=sqlite (default)       memory=postgres (workspace.yaml)
         artifacts=database→postgres        saga=postgres (workspace.yaml)
         fleet=sqlite (separate by design)
```

The absence of this is why the bug survived: every symptom was an empty screen, which reads as
"nothing ran" rather than "your data is elsewhere".

### It notices the split it just fixed

On start, if a kind resolves to a non-SQLite target **and** a workspace-local SQLite file for that
kind exists with rows, warn — naming both locations and the row count. Anyone upgrading into this
change has data in the old place, and a silent cutover would look exactly like data loss.

## Migration of call sites

Every construction site in the table above is replaced by a service call. The hardcoded ones lose
their paths entirely; `SqliteStore(workspace_path)` stays only as an internal constructor the
service uses, not as a public entry point.

`swarmkit pipeline` and `swarmkit orchestrator` build the service from the resolved workspace, so a
CLI invocation and serve agree by construction rather than by both remembering to.

A lint-style test asserts no module outside `persistence/` contains `sqlite:///` or calls
`make_engine` — the mechanical guard against this regressing, since it regressed three times.

## API shape

```python
# serve
storage = StorageService.for_workspace(workspace_path, runtime.workspace.raw)
app.state.storage = storage
app.state.store = storage.store()
app.state.saga_store = storage.saga_store()

# CLI — same two lines, so they cannot diverge
storage = StorageService.for_workspace(workspace, resolve_workspace(workspace).raw)
```

```yaml
storage:
  runtime: { backend: postgres, url: "${SWARMKIT_STORE_URL}" }   # default for every SQL store
  audit:   { backend: postgres, retention_days: 90 }             # inherits runtime's url
  checkpoints: { backend: sqlite }                               # opt a kind back out
```

Schema change: `storage.<kind>.url` becomes optional everywhere and inherits `storage.runtime.url`
when absent — repeating the same URL three times is what made the config look honoured.

## Test plan

- **Unit.** Resolution precedence per kind; a URL alone implies its backend; a per-kind block
  overrides `runtime`; an unimplementable backend raises with the missing extra named; engines are
  shared per URL and distinct across URLs.
- **The regression that started this.** A workspace declaring Postgres resolves *every* kind to
  Postgres — asserted per kind, so a newly added store that forgets the service fails the test.
- **The mechanical guard.** No `sqlite:///` literal and no `make_engine` call outside
  `persistence/`.
- **Split detection.** A non-SQLite target plus a populated local SQLite file warns, naming both.
- **Full pipeline.** Live `swarmkit serve` + `swarmkit orchestrator` + `swarmkit pipeline emit`
  against Postgres, asserting rows land in Postgres and the workspace SQLite stays empty — the
  exact scenario that failed.

## Demo plan

`just demo-storage` — one workspace, two runs:

1. `storage.runtime: postgres`, run a topology and a pipeline stage from the **CLI**, show
   `pipeline_saga`, `audit_events` and `governed_memory` all populated in Postgres and
   `.swarmkit/store.sqlite` absent.
2. Point `storage.checkpoints` at `postgres` without the extra installed and show the startup
   refusal naming it, rather than a silent SQLite fallback.

Plus the startup report block, which is the artefact an operator actually reads.

## Open questions

- **Migrating existing data.** Everyone who has run SwarmKit has rows in the old locations. A
  `swarmkit storage migrate` (copy, verify, leave the source in place) is the honest answer, but it
  is its own note — the warning above is the minimum for this change to be safe without it.
- **`agt` as an audit backend.** The schema allows it; nothing implements it. Either wire it or
  drop it from the enum. Leaving a third unimplemented value in place repeats the mistake this note
  exists to fix.
- **Postgres checkpointing.** Do we add `langgraph-checkpoint-postgres` as an optional extra, or
  remove `postgres` from the `checkpoints` enum? Adding it is more useful; removing it is more
  honest about today. It should not stay as-is.
- **Fleet separation.** Keeping enrollment credentials in a separate database is deliberate. Should
  it be *configurable* separate (a `storage.fleet` block) or *always* separate? I lean always —
  a security boundary that can be collapsed by config tends to get collapsed.
