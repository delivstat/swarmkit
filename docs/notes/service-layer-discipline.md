# Service-layer discipline

Every interface — CLI, `POST /run`, chat, both MCP front doors, A2A, webhooks, triggers, workers —
starts work and mutates state through the same backend services. This note says what those services
are, what you lose by going around them, which bypasses are legitimate, and how to add one honestly.

It exists because the rule drifted five times before anyone wrote it down.

## Why a second path is worse than it looks

A bypass does not announce itself. There is no error, no warning, no failing test — the only
symptom is **a run behaving differently depending on which door it came through**, and nobody
notices until someone asks why.

What that actually looked like:

- The `/mcp/` tool path had **no capacity gate for months**. `jobs.max_concurrent` bounded every
  other caller while an assistant could start unlimited concurrent runs.
- Chat ran the **base** version of a topology under canary, while `POST /run` of the same name ran
  the canary. Same name, different code, no indication.
- `swarmkit run --resume` never cleared the `interrupted` row it left behind, so history reported a
  failure that had not happened — for ever — while the identical resume over HTTP was fine.

In one case the *fixed* path and the *broken* one were in the same file: `server/_mcp.py`'s trigger
path went through `JobService` with a comment explaining why, twenty lines from a tool path that
did not.

## The services

| Service | Where | What using it gives you |
|---|---|---|
| `JobService` | `server/_services.py` | a durable job row, canary routing + version stamp, the concurrency semaphore, input-schema precheck, attachments, budget overrides, `source` |
| `ArtifactService` | `server/_services.py` | schema **and** workspace validation, and **rollback** — a write that breaks the workspace is unlinked |
| `WorkspaceConfigService` | `server/_workspace_config.py` | `workspace.yaml` edits with comments and key order preserved |
| `CredentialService` | `credentials/_service.py` | every credential source, OAuth refresh at the point of use, per-user identity |
| `StorageService` | `persistence/_service.py` | where data lives, decided once from config |

`canary.router_for_workspace()` is the matching helper for the one decision that is *not* a
service: canary routes come from the **workspace**, so every interface builds the router the same
way rather than one building it inline and another building none.

## The rule, and the two ways to satisfy it

**Route it through the service.** That is the default and it is usually right.

**Or mark it, with the reason.** Some call sites are legitimately different, and pretending
otherwise produces worse code than the bypass did. Put `# noqa: service-layer` on the line above
the call, with prose above that saying *why*:

```python
# An eval case is measurement, not a run someone asked for. Writing a job row per case
# would fill history with scoring traffic and make cost attribution meaningless.
# noqa: service-layer
run_result = await runtime.run(eval_set.target, case.input)
```

`tests/test_service_layer_boundary.py` enforces this. It is modelled on
`test_nothing_outside_persistence_hardcodes_a_sqlite_path`, which exists because *that* rule
regressed three times.

## Legitimately different, today

- **`server/_jobs.py::execute_job`** — the sanctioned call site. It *is* the executor.
- **`queue/_worker.py`** — calls `execute_job` directly for the same reason; the row was already
  created by `JobService.start(enqueue_only=True)`.
- **`swarmkit run`** — one-shot, no server, so the semaphore, queue and `enqueue_only` machinery do
  not apply, and its thread-id-as-job-id choice predates (and is incompatible with) `JobService`
  minting an id. Canary resolution — the part that *was* diverging — goes through the service.
- **Chat** — a turn is awaited **inline** on purpose. See below.
- **Eval** — measurement, not a requested run.

## When you fold a bespoke path into the shared one

This is the part that is easy to get wrong, and the reason chat is still inline.

The obvious story is that a bespoke path is the shared one *minus* features. Sometimes it is the
shared one *plus* lessons. Chat had accumulated two that `JobService` had not:

1. `except BaseException`, not `except Exception` — so a Ctrl-C mid-run closed the row instead of
   leaving it `running` for ever. The service caught only `Exception`, and its `finally` still
   stamped `completed_at`, producing a row both completed *and* running.
2. *"A store that will not write loses the **record** of a turn, never the turn."* The service
   writes its row unguarded, so a disk hiccup would have failed a run that otherwise worked.

Migrating chat mechanically would have fixed the unmetered-runs hole and silently introduced two
regressions, each visible only under Ctrl-C or disk failure.

So: **before you delete a bespoke path, read it for what it knows.** Then either lift those lessons
into the service (#1 was lifted — the service now handles `BaseException`) or keep the path and
share only the part that was actually diverging (#2 — chat still runs inline, but resolves its
topology through `JobService.resolve_topology`).

The question to ask is not "does this call the service?" but **"which decision is diverging?"** For
chat and the CLI the answer was *which topology runs* — a pure, store-free decision. Sharing only
that fixed the real bug and left the execution semantics that legitimately differ alone.

## See also

- `docs/notes/schema-change-discipline.md` — same shape, for schemas.
- `docs/notes/release-version-discipline.md` — same shape, for versions.
- `design/details/worker-execution.md` — why the worker executes directly.
