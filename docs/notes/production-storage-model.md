# Production storage — decision shortcut

**Recurring question:** in production, is state in a database or the filesystem?

**Answer — database for all operational state; files (git) only for artifact definitions, by design:**

- **Artifact definitions** (topologies, skills, archetypes, triggers, `StageGraph`, `RoleRegistry`) →
  **YAML files** in the workspace, git-versioned. Intentional (topology-as-data + diff + review).
- **Operational state** (jobs, run history, usage, **audit**, **review-gate queue / tasks**, approval
  records, eval, proposals) → the **runtime store backend**.
- **Checkpoints** (parked gates / approval pauses), **controller saga state** → durable store.
- **Board / per-role queues** → a derived projection (GBrain), rebuildable — not the store of record.

**Backend knob:** `SWARMKIT_STORE_BACKEND=sqlite|postgres` (or `workspace.yaml`
`storage.runtime.backend`); Postgres URL from `SWARMKIT_STORE_URL` / `DATABASE_URL`. Default SQLite
(embedded, at `{workspace}/.swarmkit/store.sqlite`) for dev/edge; **Postgres for production**. SQLite
is still a database, not loose files.

Authoritative detail + production checklist: `design/details/production-storage-model.md`.
