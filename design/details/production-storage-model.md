# Production storage model: what lives where

**Status:** reference (documents existing behaviour; no new decision)

A recurring "how does it actually run in production?" question: is state in a database or the
filesystem? The answer is **a database for all operational state; files (git) only for artifact
definitions, by design.** This note maps each thing to its store and the backend knob. It applies to
any SwarmKit deployment; the SDLC pipeline (`sdlc-pipeline-example.md`) is the worked example.

## Where each thing lives

| What | Store | Dev default | Production |
| --- | --- | --- | --- |
| **Artifact definitions** — topologies, skills, archetypes, triggers, `StageGraph`, `RoleRegistry` | **YAML files** in the workspace (topology-as-data), git-versioned; a DB-backed artifact registry at the control-plane for fleet distribution | files + git | files + git (intentional — versioning, diff, PR review) |
| **Operational state** — jobs, run history, usage/cost, **audit log**, **review-gate queue**, **approval records**, eval results, growth-loop **proposals** | **runtime store backend** (SQLAlchemy Core) | SQLite | **Postgres** |
| **Checkpoints** — parked gates / approval pauses (LangGraph `interrupt`) | LangGraph checkpointer | SQLite | **Postgres** (durable) |
| **Controller saga state** — per-requirement pipeline position, locks | the controller's own store | SQLite | **Postgres** |
| **Board / per-role task queue** | a **derived projection** (GBrain, git-backed), rebuildable from `/review` + audit | — | GBrain or live re-projection (not authoritative) |

Nothing load-bearing sits in a bare filesystem. The only things that are files are the artifact
definitions — and those are files *on purpose* (topology-as-data + git versioning + review).

## The backend knob

Backend selection (`packages/runtime/src/swarmkit_runtime/persistence/_factory.py`), by precedence:

1. `SWARMKIT_STORE_BACKEND` env var — `sqlite` | `postgres`
2. `workspace.yaml` → `storage.runtime.backend`
3. default: `sqlite`

For Postgres, the URL resolves from `SWARMKIT_STORE_URL` or `DATABASE_URL` (or
`storage.runtime.url`). SQLite lives at `{workspace}/.swarmkit/store.sqlite`. Safety valve:
`backend=postgres` with **no URL** degrades to SQLite with a warning rather than taking the runtime
down. Both the runtime and the control-plane run on SQLite *or* Postgres via SQLAlchemy Core.

So production is: `SWARMKIT_STORE_BACKEND=postgres` + `DATABASE_URL=postgres://…`.

## SQLite is still a database, not loose files

The SQLite default is an **embedded** database (one file at `{workspace}/.swarmkit/store.sqlite`) —
zero-config, right for single-node / edge (e.g. Minder runs on it). It is not "state scattered in the
filesystem". You flip to Postgres for a multi-user, multi-writer production pipeline (concurrent
approvals, cross-requirement queries, HA). The data model is identical; only the engine changes.

## Tying back to tasks and pauses

- **Role tasks** (last asked): authoritative in the runtime store — the **review-gate queue** + the
  **append-only audit** — plus the **checkpointer** (the parked gate). The GBrain board is a derived
  read-model over that, not the store of record (`task-surface-and-board.md`).
- **The approval pause itself** is a checkpoint, not a controller construct
  (`orchestration-pause-model.md`) — so in production it is a durable row in the Postgres checkpointer.

## Production checklist (SDLC pipeline)

- Runtime + control-plane: `SWARMKIT_STORE_BACKEND=postgres`, `DATABASE_URL=…`.
- LangGraph checkpointer: durable (Postgres) so weeks-long parked gates survive restarts.
- Controller: its own Postgres store for saga state.
- Artifact YAML (topologies, stage-graph, role-registry): in git; edited via the composer through the
  proposal path (`composer-pluggable-artifact-types.md`).
- Board: GBrain, or a live aggregation over `/review` + `/audit` — either is fine, it is rebuildable.

## Where this applies

- `packages/runtime/src/swarmkit_runtime/persistence/_factory.py` — backend selection (source of truth).
- `project_postgres_backend` (memory) — SQLite/Postgres via SQLAlchemy Core across both packages.
- `task-surface-and-board.md`, `orchestration-pause-model.md`, `pipeline-controller.md` — the consumers.
