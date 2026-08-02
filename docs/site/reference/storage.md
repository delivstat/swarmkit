# Storage

Where a workspace's data lives, how that is decided, and how to move it from local SQLite to
Postgres without losing what you already have.

Verified against runtime 1.130.0.

## The one rule

**Nothing chooses its own database.** Every component asks one service for a *kind* of store, and
that service resolves the configuration once, at startup, and reports what it chose.

Before 1.130.0 six components resolved storage independently and four ignored the configuration
entirely — three had a SQLite path hardcoded. A workspace declaring Postgres for everything ran its
pipeline sagas, its audit trail and its governed memory into a file on one machine. `swarmkit
validate` passed, runs succeeded, and the only symptom was that `swarmkit serve` showed nothing.

## The seven stores

| Store | Holds | Follows |
|---|---|---|
| `runtime` | jobs, conversations, usage, serve access | the config you write |
| `audit` | the append-only audit trail | `storage.audit`, else `storage.runtime` |
| `artifacts` | pipeline stage outputs | `storage.artifacts`, else `storage.runtime` |
| `saga` | pipeline state + the durable event queue | `storage.runtime` |
| `memory` | governed memory + its change log | `storage.runtime` |
| `fleet` | enrollment tokens, fleet memberships | `storage.runtime` (design 19 Q4) |
| `checkpoints` | LangGraph run state | **only** `storage.checkpoints` |

`audit` and `fleet` keep their own SQLite *file* when the backend is SQLite — separate retention,
separate size — but they follow the same *backend*.

**`checkpoints` is the exception, deliberately.** It is a LangGraph component with its own driver
(`pip install "swarmkit-runtime[postgres]"`), so promoting it to Postgres merely because the
application store is Postgres would fail a workspace that never asked for it — at startup, on
configuration nobody wrote. Only an explicit `storage.checkpoints` block moves it.

## Configuring it

One block moves the whole workspace:

```yaml
# workspace.yaml
storage:
  runtime:
    backend: postgres
    url: ${SWARMKIT_STORE_URL}
```

A per-store block inherits `storage.runtime.url` when it declares none, so the URL is written once:

```yaml
storage:
  runtime:
    backend: postgres
    url: ${SWARMKIT_STORE_URL}
  audit:
    retention_days: 90        # different retention, same database
```

`${VAR}` and `${VAR:-default}` are expanded here. (Before 1.130.0 they were not — `url:
${SWARMKIT_STORE_URL}`, the form every deployment doc uses, reached SQLAlchemy as those literal
characters.)

## `SWARMKIT_STORE_URL` vs `SWARMKIT_STORE_BACKEND`

Both are environment variables, both override `workspace.yaml`, and they are **not** a pair.

| Variable | What it does | Needed? |
|---|---|---|
| `SWARMKIT_STORE_URL` | The connection URL. **A URL names its own backend**, so setting this alone selects Postgres. | This is the one you want. |
| `DATABASE_URL` | Same, used only when `SWARMKIT_STORE_URL` is unset. | Fallback. |
| `SWARMKIT_STORE_BACKEND` | Forces `sqlite` or `postgres` regardless of the file. | Rarely. Only to force SQLite while a URL is set. |

```bash
# Sufficient. Do not also set SWARMKIT_STORE_BACKEND.
export SWARMKIT_STORE_URL="postgresql://swarm:secret@db:5432/swarmkit"
```

!!! warning "This exact combination was a bug until 1.130.0"

    A `.env` declaring only `SWARMKIT_STORE_URL` was **silently ignored**: the resolver required
    `SWARMKIT_STORE_BACKEND` to be set before it would look at the URL at all, so a correctly
    configured Postgres stayed empty while everything wrote to SQLite. If you set only the URL and
    saw no data, that was this.

The environment is a *global* signal — it moves every store that follows `storage.runtime`. It does
**not** move `checkpoints`, for the reason above.

## Seeing what it chose

```bash
swarmkit storage status <workspace>
```

```
storage for /srv/swarm:

  store        backend   location  (source)
  runtime      postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
  audit        postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
  checkpoints  sqlite    workspace-local  (default)
  artifacts    postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
  memory       postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
  saga         postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
  fleet        postgres  postgresql://swarm:***@db:5432/swarmkit  (env)
```

The `source` column is the point: it names the setting that won, so "I set that and it did nothing"
has an answer. The same report is printed at `swarmkit serve` startup, served at `GET /storage`, and
shown on the web UI's **System** page — the answer to "why is this screen empty" has to be reachable
from the screen that is empty.

Passwords are masked everywhere. This output goes to terminal scrollback, log files and CI capture.

## It fails rather than degrades

A backend that cannot be honoured raises at startup:

```
storage backend 'postgres' for runtime (from storage.runtime) has no URL. Set one of:
storage.runtime.url, SWARMKIT_STORE_URL. (If the value is '${VAR}', that variable is unset.)
Refusing to fall back to sqlite: the run would write to a different database than the one
configured.
```

Falling back would write the run somewhere other than where you configured, and split `serve` from
the `orchestrator` with neither process warning. A failed start is the cheaper failure.

## Moving from local SQLite to Postgres

The whole runbook. Steps 4 and 5 are the ones people skip.

### 1. Create the database

```bash
createdb swarmkit
# or: docker run -d --name swarmkit-pg -e POSTGRES_PASSWORD=secret \
#       -e POSTGRES_USER=swarm -e POSTGRES_DB=swarmkit -p 5432:5432 postgres:16
```

Nothing else — the tables are created on first connection.

### 2. Point the workspace at it

```yaml
# workspace.yaml
storage:
  runtime:
    backend: postgres
    url: ${SWARMKIT_STORE_URL}
```

```bash
# .env, or your process manager's environment
SWARMKIT_STORE_URL=postgresql://swarm:secret@localhost:5432/swarmkit
```

Keep the URL in the environment and the *reference* in version control. Do not put a password in
`workspace.yaml`.

### 3. Confirm the resolution before moving anything

```bash
swarmkit storage status .
```

Every store you expect should read `postgres`, and the `source` column should name the setting you
just wrote. If one still says `sqlite (default)`, fix that first — migrating into a database the
runtime is not going to use is worse than not migrating.

### 4. Copy the existing rows

```bash
swarmkit storage migrate . --dry-run    # what would move
swarmkit storage migrate .              # move it
```

```
Migrating 468 row(s) from /srv/swarm/.swarmkit:
  store.sqlite   jobs                                18 -> postgresql://swarm:***@db:5432/swarmkit
  store.sqlite   pipeline_saga                       11 -> postgresql://swarm:***@db:5432/swarmkit
  audit.sqlite   audit_events                       412 -> postgresql://swarm:***@db:5432/swarmkit
  ...
Done: 468 row(s) copied, 0 already present.
```

- **Additive and idempotent.** Rows already present are skipped on primary key, so a re-run after a
  partial failure resumes rather than duplicates.
- **Nothing is deleted.** The SQLite files stay exactly as they are.
- Run it with the runtime **stopped**, so nothing is writing to the old files mid-copy.

Without this step, "switch to Postgres" means "abandon everything recorded so far". Governed memory
is the one that actually hurts: it is accumulated knowledge, not just history.

### 5. Verify, then archive the old files

```bash
swarmkit storage status .        # no warnings
psql -d swarmkit -c "SELECT count(*) FROM audit_events;"
```

`status` warns while a populated local SQLite still exists under a remote configuration:

```
! audit: configured for postgres, but /srv/swarm/.swarmkit/audit.sqlite still holds ~412 rows
  written before this. Move them with:  swarmkit storage migrate /srv/swarm
```

Once the counts match, move the files aside:

```bash
mkdir -p .swarmkit/pre-postgres
mv .swarmkit/store.sqlite .swarmkit/audit.sqlite .swarmkit/fleet.sqlite .swarmkit/pre-postgres/
```

Leaving them in place is how a split brain starts.

### 6. Restart everything that touches the store

`swarmkit serve`, `swarmkit orchestrator`, and any process manager unit. **They must all see the
same environment.** An orchestrator started without `SWARMKIT_STORE_URL` polls a different database
than the serve that enqueued the work: no stage ever runs, and neither process warns.

### Optionally: Postgres checkpoints too

Run state does not need to move — SQLite checkpoints are local and disposable, and losing them
costs you resumability, not data. If you want them shared anyway:

```bash
pip install "swarmkit-runtime[postgres]"
```

```yaml
storage:
  checkpoints:
    backend: postgres        # inherits storage.runtime.url
```

## Rolling back

Remove the `storage:` block (or set `backend: sqlite`), restore the archived files, restart. The
SQLite files were never modified, so this is a move-back, not a restore.

## See also

- [Workspace environment configuration](env-config.md) — `workspace.env.yaml`, `${...}` references,
  and marking properties as secret.
- [Orchestrator integration](orchestrator-integration.md) — why serve and the orchestrator must
  resolve the same store.
- [CLI reference](cli.md) — `swarmkit storage`, `swarmkit system`.
