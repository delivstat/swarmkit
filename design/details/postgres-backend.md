# Postgres persistence backend

Status: in progress (design + rollout). Goal: run every SwarmKit store on **either SQLite or
Postgres** from one config switch, so a distributed multi-node deployment (multiple runtime nodes
+ a central fleet control plane) can share a Postgres database instead of per-node SQLite files.

See design §14 (runtime persistence) and `distributed-architecture.md` (Step 1 = single-process
SQLite, "Postgres-ready"). The store `_factory.py` + `SWARMKIT_STORE_BACKEND` env var + a
`storage.runtime.{backend,url}` config field already exist; today the `postgres` branch logs
"not yet implemented, falling back to sqlite". This makes it real.

## Decisions

- **One implementation per store, via SQLAlchemy Core** (not two parallel raw-SQL impls). Each
  store defines its tables once as SQLAlchemy `Table` metadata and issues dialect-agnostic Core
  statements (`insert()`/`select()`/`update()`), so the same code path serves both dialects. No
  hand-written `?`-vs-`%s` duplication, no ORM.
- **Driver: psycopg 3** (`postgresql+psycopg://…`), which SQLAlchemy drives in both sync and async
  modes. SQLite uses the stdlib driver (sync) and `aiosqlite` (async, for the audit engine only).
- **URL, not path, is the seam.** A store is built from a SQLAlchemy URL:
  - sqlite (default): `sqlite:///{workspace}/.swarmkit/store.sqlite`
  - postgres: the configured URL (`SWARMKIT_STORE_URL` / `DATABASE_URL` / `storage.runtime.url`).
  The back-compat `SqliteStore(workspace_path)` constructor is kept (builds the sqlite URL).
- **Schema bootstrap: `metadata.create_all(engine)`** — idempotent, matches the existing column
  layout exactly so existing SQLite DB files keep working. Alembic migrations are a later concern
  (noted as out of scope here).
- **WAL for SQLite stays**; a `PRAGMA journal_mode=WAL` is issued on connect for the sqlite
  dialect only (via a SQLAlchemy `connect` event), preserving current concurrency behaviour.

## Scope (per the user: runtime + control-plane together)

| Store | Package | Sync/async | Tables |
| --- | --- | --- | --- |
| persistence (jobs/conversations/usage/serve_access) | runtime | sync | 4 |
| audit log | runtime | **async** (AuditProvider ABC is async) | 1 |
| registry (instances/commands) | control-plane | sync | 2 |
| artifacts (versions/deployments/reported) | control-plane | sync | 3 |
| proposals | control-plane | sync | 1 |
| aggregation | control-plane | sync | n |

Control-plane stays **standalone** (design D1): it gains `sqlalchemy` + `psycopg` as its **own**
dependencies — never a `swarmkit-runtime` import. The SQLAlchemy engine/bootstrap boilerplate is
tiny and lives in each package independently (mirrors how the verb table is duplicated + pinned by
a contract test rather than shared via an import).

## Dependencies added

- runtime: `sqlalchemy>=2.0`, `psycopg[binary]>=3.1`, `aiosqlite>=0.20` (async audit sqlite engine).
- control-plane: `sqlalchemy>=2.0`, `psycopg[binary]>=3.1` (sync stores only — no aiosqlite).

## Test strategy

- **SQLite remains the CI default** — every existing store test runs unchanged against sqlite, so
  the rewrite is guarded by the current suites (behaviour must be identical).
- A **Postgres suite** runs the same store contract against a real Postgres **only when
  `SWARMKIT_TEST_POSTGRES_URL` is set** (marked `integration`, deselected by default like the other
  live-service tests). CI stays sqlite-only; developers/CD can point it at a Postgres to verify the
  dialect. A `pytest-postgresql` ephemeral instance is a possible later add.

## Rollout (one PR per row, each behaviour-preserving + green on the existing suite)

1. **PR-1 — runtime persistence on SQLAlchemy Core.** `_tables.py` (Table metadata matching the
   current schema) + engine-based `Store` with `SqliteStore(Store)` back-compat; `_factory` builds
   the engine (sqlite default / postgres from URL) and returns a `Store`. Existing
   `test_persistence` + `test_store_factory` pass unchanged.
2. **PR-2 — runtime audit provider on async SQLAlchemy** (`create_async_engine`, aiosqlite/psycopg).
3. **PR-3 — control-plane stores on SQLAlchemy Core** (registry/artifacts/proposals/aggregation),
   folding the `_sqlite_base` connection helper into a SQLAlchemy engine.

## Non-goals (this feature)

Alembic migrations, connection-pool tuning / read replicas, and a Postgres audit-log partitioning
strategy. The append-only audit invariant (§8.3) is unchanged — no UPDATE/DELETE exposed.
