---
title: Eval on serve — run eval sets over HTTP, reports in the store
description: One EvalService behind `swarmkit eval` and new `/evals` routes on `swarmkit serve`; an eval run holds one capacity slot, its cases stay measurement runs, and reports move from files to the store so comparison works on any deployment.
tags: [eval, serve, service-layer, storage]
status: draft
---

# Eval on serve

**Scope:** `packages/runtime` (eval, server, persistence, CLI)
**Design reference:** [[eval-harness]] (M15 slices 1 + 2), CLAUDE.md invariant 9, `docs/notes/service-layer-discipline.md`
**Status:** draft. First half of #1105, and the baseline for capturing runs as cases (#1105 part 2) and for gate labels (#1106).

## Goal

A `swarmkit serve` deployment can list its eval sets, run one, follow it, and compare the report with the previous run. It goes through the same service the CLI uses, and reports live in the workspace's store rather than in local files.

## Why now

- **Eval is CLI-only.** `swarmkit eval` (`cli/_cmd_run.py:579`) is the only way in. A deployment managed over HTTP, by the portal, the fleet panel or CI against a running instance, can't score itself.
- **Reports are files.** They're written to `.swarmkit/eval-results/<set>-<stamp>.json`, and `latest_prior_report` (`eval/_compare.py:26`) globs that directory. On a multi-instance or Postgres deployment each instance sees only its own files, so `--compare` compares against whatever ran on that box.
- **#1105 and #1106 need a home.** Captured and labelled cases are only useful once a deployment can run them.

## What does *not* change: per-case runs stay off `JobService`

The runner calls `runtime.run(eval_set.target, case.input)` directly, marked `# noqa: service-layer` with the reason (`eval/_runner.py:80-84`). That's the worked example in `docs/notes/service-layer-discipline.md`: an eval case is measurement, not a run someone asked for. A job row per case would fill history with scoring traffic and make cost attribution meaningless.

That reasoning holds on serve too, so this design keeps it. What serve needs is narrower:
1. one service both front doors use;
2. a capacity gate around the eval as a whole;
3. a durable place for the report.

## Non-goals

- **Capture (#1105 part 2) and gate labels (#1106).** This is the baseline they build on.
- **Writing eval sets over HTTP.** Sets stay files under `<workspace>/evals/`, delivered the way topologies are (git, image, fleet sync). `PUT /evals/{id}` arrives with capture, which is its first writer.
- **The queue tier.** `serve --role api` doesn't execute work. An eval run there is refused with 409, pointing at an all-in-one serve, a worker-capable instance or the CLI. Evals as claimable queue work is a separate decision.
- **Canary routing for cases.** An eval measures a named version: the set's `target`, which may be `name@version`. Routing cases through the canary split would score a mix of versions in one report.
- **Portal UI.** A later slice reads the same routes.
- **The fleet panel's `run_eval`** (`control-plane/_connector.py:352`). It runs an authoring eval *topology* through `POST /run`, which is a different thing. It could move to `/evals` later.

## Design

### `EvalService`: one service, both front doors

`eval/_service.py` exports `EvalService(runtime, store)`:

```python
class EvalService:
    def list_sets(self) -> list[EvalSetSummary]: ...           # id, description, target, case count
    def get_set(self, set_id: str) -> EvalSet: ...             # NotFoundError / InvalidRequestError
    async def run(
        self, set_id: str, *, source: str, on_case: Callable[[EvalCaseResult], None] | None = None,
        eval_run_id: str | None = None,
    ) -> StoredEvalRun: ...                                    # runs, stores, compares
    def get_run(self, eval_run_id: str) -> StoredEvalRun | None: ...
    def list_runs(self, set_id: str | None = None, *, limit: int = 20) -> list[EvalRunSummary]: ...
```

`run()` does four things in order:
1. It reads the previous **finished** report for the set from the store.
2. It creates the `eval_runs` row as `running`.
3. It calls the unchanged `run_eval_set`, appending each case result to the row as it finishes, so progress can be polled.
4. It writes the final report, and the `ReportDiff` against step 1's report, then marks the row `succeeded` or `failed`. `failed` means one or more cases failed; `error` means the eval itself couldn't run.

The CLI and the routes are thin over it. Neither touches `run_eval_set` or the store directly, and the boundary test gets a matching rule (see test plan).

### Storage: an `eval_runs` table

```python
eval_runs = Table(
    "eval_runs", metadata,
    Column("id", Text, primary_key=True),             # uuid
    Column("eval_set_id", Text, nullable=False, index=True),
    Column("target", Text, nullable=False),
    Column("status", Text, nullable=False),           # running | succeeded | failed | error
    Column("source", Text, nullable=False),           # cli | serve
    Column("passed", Integer, default=0),
    Column("total", Integer, default=0),
    Column("report", Text),                           # EvalReport.to_dict() JSON, cases so far
    Column("diff", Text),                             # ReportDiff JSON vs the previous run, or null
    Column("cost_usd", Float, default=0.0),           # summed from each case's RunResult.usage
    Column("error", Text),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
)
```

- It lives in the runtime store (`StorageService.store()`), so it's SQLite or Postgres as the workspace says, and is created by `create_all_idempotent`.
- It's added to `swarmkit storage migrate`'s explicit `_TABLES` list (`cli/_cmd_storage.py:24`). Leaving it out would silently drop eval history in a SQLite→Postgres move.
- **Cost on the report.** Each case's `RunResult.usage` is summed into `cost_usd`, so "what did this eval cost" is answered on the eval, not mixed into job usage. That's the attribution the no-job-row rule protects.
- **Files are read but no longer written.** `--compare` falls back to the newest `.swarmkit/eval-results/` file when the store has no prior run for the set, so existing history still counts. New reports go to the store only. `--output PATH` still writes the JSON where asked, because CI uses it.
- **Retention:** `storage.evals.retention_days`, defaulting to the audit's 365 days (`persistence/_service.py:402`). Old rows are pruned when a run is written. ([[traces-in-the-store]] is still a draft, so there is no trace window to borrow.)

### Routes on serve

| Method + path | Tier | What it does |
|---|---|---|
| `GET /evals` | read | the workspace's eval sets |
| `GET /evals/{set_id}` | read | one set, parsed (cases included) |
| `POST /evals/{set_id}/run` | run | start an eval; 202 + `{eval_run_id, status: "running"}` |
| `GET /evals/runs` | read | recent eval runs, `?set=` filter |
| `GET /evals/runs/{eval_run_id}` | read | status, cases so far, final report, diff, cost |

- **Tiers come from the existing mapping** (`server/_helpers.py:157`): GETs are `read`, and a POST outside `/api/` is `run`, the same as `POST /run`. No new scope is needed.
- **The capacity gate.**
  - An eval run takes **one slot** on `app.state.job_semaphore` for its whole duration, and is refused with 429 when the semaphore is full. That's the same reject-when-full rule `JobService.start` uses.
  - Cases run one at a time inside that slot, as on the CLI. An eval can't starve real traffic of more than one slot, and it can't run unbounded beside it.
- **Background execution.**
  - The route returns 202 and runs the eval in a task, kept in a registry the way the job store keeps its tasks.
  - A per-case timeout is the serve's `timeout_seconds`. A case that times out fails with `error: timeout`, and the eval carries on.
- **The API tier.** With `enqueue_only` set, `POST /evals/{id}/run` answers 409.
- **Stopping** is in v1 as `POST /evals/runs/{id}/stop`. It cancels after the current case and marks the run `error: stopped`. The stop is cooperative, between cases, so no case is left half-scored.

### CLI

`swarmkit eval <workspace> <set>` keeps its flags, output and exit codes. Underneath it calls `EvalService.run(source="cli")`. The visible change is the report line, which says where the report went:

```
4/5 passed (80%) · eval run 3f2a… stored in sqlite:.swarmkit/store.sqlite
```

New, matching the routes: `swarmkit eval runs <workspace> [--set S]` and `swarmkit eval show <workspace> <eval_run_id>`.

## Implementation plan

Two PRs, each reviewable on its own against a merged baseline.

**PR 1: `EvalService` and reports in the store (no serve changes).**
1. Add the `eval_runs` table, `Store` methods (`create_eval_run`, `append_eval_case`, `finish_eval_run`, `get_eval_run`, `list_eval_runs`, `latest_finished_eval_run`, `prune_eval_runs`), and the migrate list entry.
2. Add `eval/_service.py` with `EvalService`. `run_eval_set` gains per-case usage (`RunResult.usage` exists but isn't kept), an optional per-case timeout, and a stop check between cases. All three default off, so the CLI behaves as before.
3. Move `swarmkit eval` onto the service, falling back to the old report files for `--compare`, and add `eval runs` and `eval show`.
4. Add the boundary rule: only `eval/_service.py` (and tests) may call `run_eval_set`.
5. Docs: `eval-harness.md` (slice 3 pointer), the CLI reference, llms.txt and its regeneration.

**PR 2: `/evals` routes on serve.**
1. Add `server/_routes_evals.py`: the five routes plus stop, the semaphore slot, the task registry, and 409 on the API tier.
2. Register it in `_app.py`.
3. Docs: the serve API reference, llms.txt, and `just demo-eval-serve`.

Each PR bumps `swarmkit-runtime` with a minor version, because both add surface. The schema is unchanged: `EvalSet` is a pydantic model in the runtime with no JSON Schema in `packages/schema`. If PR 1 review wants one, that becomes its own schema-discipline PR first.

## Test plan

**PR 1**
- **Unit, `EvalService`:**
  - run → row `running` → cases appended in order → `succeeded` or `failed` with the report and diff;
  - a set that can't load → `error` with no row left `running`;
  - the diff is taken against the previous *finished* run, never one still running.
- **Store, on both backends (the `postgres` marker pattern):** round trip, `list_eval_runs` ordering and filtering, retention pruning, and `storage migrate` copying `eval_runs`.
- **CLI:**
  - the existing `test_eval_cli.py` still passes unchanged, with the same output and exit codes;
  - `--compare` reads the prior run from the store, and falls back to a legacy report file when the store has none;
  - `--output` still writes the file.
- **Cost:** a stub runtime returning usage → `cost_usd` on the row equals the sum.
- **Boundary:** a new `run_eval_set(` call outside `eval/_service.py` fails `test_service_layer_boundary.py`.

**PR 2**
- **Routes:**
  - `POST /evals/{id}/run` returns 202, and polling `GET /evals/runs/{id}` sees cases arrive, then a terminal status;
  - 404 for an unknown set, 409 on `enqueue_only`, 429 with the semaphore full;
  - `read` can GET but not POST, and `run` can POST.
- **Capacity:** while an eval runs, it holds exactly one semaphore slot, and the slot is released on success, failure, stop and exception.
- **Stop:** stopping mid-run finishes the current case, then reports `error: stopped` with the cases done so far.
- **Same service:** an eval started over HTTP and one from the CLI produce rows of the same shape that differ only in `source`, and each one's `diff` sees the other as its prior run.

## Demo plan

`just demo-eval-serve` (in `examples/`, with mock providers, so it needs no key):
1. start `swarmkit serve` on a workspace with a three-case eval set;
2. `curl POST /evals/<set>/run`, poll until done, and print the report and cost;
3. change the topology's prompt so one case breaks, reload, and run again over HTTP;
4. print the diff showing the regressed case;
5. run `swarmkit eval runs` on the CLI, which lists both runs from the same store.

## Open questions

- **A serve restart mid-eval.** The task dies, and the row stays `running`. v1 marks a row `error: interrupted` when it's read and its `started_at` is older than `cases × timeout_seconds`. Alternatively, the serve could sweep rows it owns at startup, but that needs an instance id on the row. Which one?
- **Running cases concurrently** inside the slot (`--parallel N`), for large sets. It's deferred: it would take N slots, and makes per-case cost and ordering harder to read. Worth it once captured sets get big?
- **A JSON Schema for `EvalSet`** in `packages/schema`. Today it's only pydantic. Captured sets written by a machine are the point where an external validator starts to matter.
