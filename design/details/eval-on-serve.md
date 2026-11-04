---
title: Eval on serve — eval sets as schema'd artifacts, run, recovered and reviewed over HTTP
description: An eval-set JSON Schema; one EvalService behind `swarmkit eval` and new serve routes; an eval run holds one capacity slot, its cases stay measurement runs, it is swept or resumed after a restart like a job, and reports live in the store; the portal lists, reviews, edits and runs eval sets.
tags: [eval, serve, service-layer, storage, schema, ui]
status: in-progress
---

# Eval on serve

**Scope:** `packages/schema` (eval-set schema), `packages/runtime` (eval, server, persistence, CLI), `packages/ui` (Evals page)
**Design reference:** [[eval-harness]] (M15 slices 1 + 2), CLAUDE.md invariant 9, `docs/notes/service-layer-discipline.md`, `docs/notes/schema-change-discipline.md`
**Status:** implemented. PR 1 (the schema, 1.266.0), PR 2 (the service, the store and recovery, 1.267.0), PR 3 (serve routes, 1.268.0) and PR 4 (the portal's Evals page, swarmkit-webui 0.30.0) have shipped. First half of #1105, and the baseline for capturing runs as cases (#1105 part 2) and for gate labels (#1106).

## Goal

On a `swarmkit serve` deployment, eval sets can be seen, reviewed, edited and run, from the portal or over HTTP. A run can be followed, survives a restart the way a job does, and is compared with the previous run. The CLI and serve go through the same service and read the same store.

## Why now

- **Eval is CLI-only.** `swarmkit eval` (`cli/_cmd_run.py:579`) is the only way in. A deployment managed over HTTP, by the portal, the fleet panel or CI against a running instance, can't score itself.
- **Reports are files.** They're written to `.swarmkit/eval-results/<set>-<stamp>.json`, and `latest_prior_report` (`eval/_compare.py:26`) globs that directory. On a multi-instance or Postgres deployment each instance sees only its own files, so `--compare` compares against whatever ran on that box.
- **Eval sets aren't a first-class artifact.**
  - There's no JSON Schema, only a runtime pydantic model (`eval/_models.py`).
  - Serve can't list or write them (`server/_services.py:40` has no kind for them).
  - The portal can't show them.
  - Cases are the test gate in growth through authoring (§12), so a person reviewing what gets tested should see them in the UI, not only in git.
- **#1105 and #1106 need a home.** Captured and labelled cases are only useful once a deployment can review and run them.

## What does *not* change: per-case runs stay off `JobService`

The runner calls `runtime.run(eval_set.target, case.input)` directly, marked `# noqa: service-layer` with the reason (`eval/_runner.py:80-84`). That's the worked example in `docs/notes/service-layer-discipline.md`: an eval case is measurement, not a run someone asked for. A job row per case would fill history with scoring traffic and make cost attribution meaningless.

That holds on serve too. What serve needs is:
1. one service both front doors use;
2. a capacity gate around the eval as a whole;
3. a durable, recoverable record of the run.

## Non-goals

- **Capture (#1105 part 2) and gate labels (#1106).** This is the baseline they build on.
- **Running cases in parallel.** Cases run one at a time in v1. Parallelism is planned (see [Later](#later-parallel-cases)), but nothing in this design reserves a field for it.
- **The queue tier.** `serve --role api` doesn't execute work. An eval run there is refused with 409, pointing at an all-in-one serve or the CLI. Evals as claimable queue work is a separate decision.
- **Canary routing for cases.** An eval measures a named version: the set's `target`, which may be `name@version`. Routing cases through the canary split would score a mix of versions in one report.
- **The fleet panel's `run_eval`** (`control-plane/_connector.py:352`). It runs an authoring eval *topology* through `POST /run`, which is a different thing. It could move to these routes later.

## Design

### 1. `eval-set.schema.json`: eval sets become a schema'd artifact

- **The schema** goes in `packages/schema/schemas/eval-set.schema.json`, following `schema-change-discipline.md` in full:
  - the bundled copy;
  - valid and invalid fixtures;
  - pydantic and TS codegen;
  - a design record in `design/details/eval-set-schema-v1.md`.
- **The shape is today's**, so every existing eval set validates unchanged:
  - `apiVersion: swarmkit/v1`, `kind: EvalSet`;
  - `metadata.id` (the kebab-id pattern) and `description`;
  - `target`;
  - `cases[]` of `{id, input, expect}`.
  - `expect` keeps its fields: `contains`, `not_contains`, `regex`, `equals`, `not_empty`, `used_skills`, `judge`, `rubric`, `min_confidence`.
- **`title` and `description` on every field**, as the other artifact schemas carry, let the portal's `SchemaForm` render it with no per-kind form code.
- **Case ids must be unique within a set.** Enforced by the validator, because reports and diffs are keyed by case id.
- **The runtime's `EvalSet`** is replaced by the generated model, or checked against it in a test. The loader validates with `swarmkit_schema` before building the model, so the CLI, serve and the portal accept exactly the same files.

### 2. `EvalService`: one service, both front doors

`eval/_service.py` exports `EvalService(runtime, store)`:

```python
class EvalService:
    def list_sets(self) -> list[EvalSetSummary]: ...           # id, description, target, case count, last run
    def get_set(self, set_id: str) -> EvalSet: ...             # NotFoundError / InvalidRequestError
    async def run(
        self, set_id: str, *, source: str, eval_run_id: str | None = None,
        on_case: Callable[[EvalCaseResult], None] | None = None,
        case_timeout_s: float | None = None, should_stop: Callable[[], bool] | None = None,
    ) -> StoredEvalRun: ...
    async def resume(self, eval_run_id: str, *, source: str, ...) -> StoredEvalRun: ...
    def get_run(self, eval_run_id: str) -> StoredEvalRun | None: ...
    def list_runs(self, set_id: str | None = None, *, limit: int = 20) -> list[EvalRunSummary]: ...
```

`run()` does five things in order:
1. It reads the previous **finished** run for the set from the store.
2. It creates the `eval_runs` row as `running`, with a **snapshot of the set** (the cases as they were when the run started). A resume, a review and a diff then always refer to what was actually run, even if the set is edited afterwards.
3. It calls `run_eval_set`, one case at a time. After each case it appends the result, adds the case's cost, and moves `heartbeat_at` forward.
4. Before each case it checks `should_stop`. A stop takes effect between cases, so no case is left half-scored.
5. It writes the final report and the `ReportDiff` against step 1's run, then marks the row as one of these terminal statuses:
   - `succeeded`: all cases pass;
   - `failed`: one or more cases fail;
   - `stopped`;
   - `error`: the eval itself couldn't run.

The CLI and the routes are thin over it. A boundary-test rule allows calls to `run_eval_set` only from `eval/_service.py`.

### 3. Storage: an `eval_runs` table

```python
eval_runs = Table(
    "eval_runs", metadata,
    Column("id", Text, primary_key=True),             # uuid
    Column("eval_set_id", Text, nullable=False, index=True),
    Column("target", Text, nullable=False),
    Column("status", Text, nullable=False),           # running | succeeded | failed | stopped | interrupted | error
    Column("source", Text, nullable=False),           # cli | serve
    Column("eval_set", Text, nullable=False),         # the set as run (JSON snapshot)
    Column("report", Text),                           # EvalReport.to_dict() JSON — cases so far
    Column("diff", Text),                             # ReportDiff JSON vs the previous finished run
    Column("passed", Integer, default=0),
    Column("total", Integer, default=0),
    Column("cost_usd", Float, default=0.0),           # summed from each case's RunResult.usage
    Column("error", Text),
    Column("started_at", Text, nullable=False),
    Column("heartbeat_at", Text, nullable=False),     # moves on every recorded case
    Column("finished_at", Text),
)
```

- It lives in the runtime store (`StorageService.store()`), so it's SQLite or Postgres as the workspace says, and is created by `create_all_idempotent`.
- It's added to `swarmkit storage migrate`'s explicit `_TABLES` list (`cli/_cmd_storage.py:24`). Leaving it out would silently drop eval history in a SQLite→Postgres move.
- **Cost belongs to the eval.** Each case's `RunResult.usage` is summed into `cost_usd`, so "what did this eval cost" is answered on the eval, not mixed into job usage. That's the attribution the no-job-row rule protects.
- **Files are read but no longer written.** `--compare` falls back to the newest `.swarmkit/eval-results/` file when the store has no prior run for the set, so existing history still counts. New reports go to the store only. `--output PATH` still writes the JSON where asked, because CI uses it.
- **Retention:** `storage.evals.retention_days`, defaulting to the audit's 365 days (`persistence/_service.py:402`). Old rows are pruned when a run is written.

### 4. Restart: recovered or marked, the way a job is

Jobs handle a dead process in two steps. Eval runs do the same.

- **The sweep at startup.**
  - `_sweep_stale_jobs` (`server/_app.py:111`) marks rows left `running` as `interrupted`, but only rows older than the job timeout. That's because several instances can share one Postgres store, and a blanket sweep would close another live instance's work (`persistence/_store.py:505`).
  - Eval runs get the same sweep, `sweep_stale_eval_runs`, called beside it and on CLI startup of `swarmkit eval`.
  - An eval is longer than any one case, so the bound is on **`heartbeat_at`**, not `started_at`. A live eval records a case at least every `timeout_seconds`, so a row whose heartbeat is older than that has no owner.
  - The status is `interrupted`, not `failed`: the cases recorded so far are real, and nothing more is claimed.
- **Resume.**
  - `POST /evals/runs/{id}/resume` and `swarmkit eval resume <workspace> <eval_run_id>` continue an `interrupted` or `stopped` run.
  - Resume runs only the cases in the **snapshot** that have no result yet, on the same row. Recorded case results are the eval's checkpoint, so nothing is re-run or double-charged.
  - It's refused (409) for a run in any other status, and for one whose target topology no longer exists.
- **Not automatic.** As with jobs, the sweep marks the row and a person or caller decides whether to resume. An eval that resumes itself on every restart could spend money in a crash loop.

### 5. Routes on serve

**Eval sets: an artifact kind, through `ArtifactService`.**

| Method + path | Tier | What it does |
|---|---|---|
| `GET /api/evals` | read | every eval set: id, description, target, case count, last run's status and pass rate |
| `GET /api/evals/{id}` | read | one set, parsed |
| `GET /api/evals/{id}/yaml` | read | its YAML |
| `POST /api/evals` · `PUT /api/evals/{id}` | admin | create / replace |
| `DELETE /api/evals/{id}` | admin | remove the file; its run history stays |

- `ArtifactService` gets an `eval-set → evals/` kind.
- A write is validated against the schema, its `target` must exist in the workspace, and the previous file is restored on failure. That's the same write-validate-rollback every artifact write gets.
- The paths sit under `/api/*`, so the existing tier mapping (`server/_helpers.py:157`) makes reads `read` and writes `admin`, with no new scope.
- **Who may edit a test.**
  - Eval-set writes are **not exposed to agents**: no MCP tool, no authoring-swarm write path.
  - An agent that can edit the cases it's measured against can pass any test. The same reasoning reserves `topologies:modify` for people (invariant 6).
  - A test asserts that no agent-facing tool writes under `evals/`.

**Runs.**

| Method + path | Tier | What it does |
|---|---|---|
| `POST /evals/{id}/run` | run | start; 202 + `{eval_run_id, status: "running"}` |
| `GET /evals/runs` | read | recent runs, `?set=` filter |
| `GET /evals/runs/{run_id}` | read | status, the set as run, cases so far, report, diff, cost |
| `POST /evals/runs/{run_id}/stop` | run | stop after the current case |
| `POST /evals/runs/{run_id}/resume` | run | continue an interrupted or stopped run |

- **The capacity gate.**
  - An eval run takes **one slot** on `app.state.job_semaphore` for its whole duration, and is refused with 429 when the semaphore is full. That's the reject-when-full rule `JobService.start` uses.
  - Cases run one at a time inside that slot. An eval can't take more than one slot from real traffic, and can't run unbounded beside it.
- **Background execution.** The route returns 202 and runs the eval in a task, kept in a registry the way the job store keeps its tasks. The per-case timeout is the serve's `timeout_seconds`. A case that times out fails with `error: timeout`, and the eval carries on.
- **The API tier.** With `enqueue_only` set, `run` and `resume` answer 409.

### 6. The portal: an Evals page

`packages/ui/app/evals/` follows the skills and funnels pages, reading the routes above:

- **List:** every set, with its target, case count, and the last run's status, pass rate and date.
- **Review a set:** its cases, each showing input and expectations, and each case's result in recent runs. A case that has regressed is visible at a glance.
- **Edit:** a `SchemaForm` from `eval-set.schema.json`, plus the YAML view the other artifact pages have, saved through `PUT /api/evals/{id}`. A validation error shows on the field it belongs to.
- **Run and follow:** start a run, see cases arrive by polling `GET /evals/runs/{id}`, and stop or resume it.
- **A run's report:** per-case checks with their details, the diff against the previous run (regressed, fixed, new), and the cost.

This bumps `swarmkit-webui`, and the `[ui]` floor in the runtime moves to the release that adds the routes, per invariant 8.

## Implementation plan

Four PRs, in order. Each is reviewable against a merged baseline.

**PR 1: eval-set schema** (`packages/schema`, schema minor bump)
1. Add `eval-set.schema.json` with UI hints, the bundled copy, fixtures (every eval set in `examples/` and the tests must validate), pydantic and TS codegen, and `eval-set-schema-v1.md`.
2. In the runtime loader, validate with `swarmkit_schema`, and either replace `EvalSet` with the generated model or test that the two agree.
3. Floor the runtime's `swarmkit-schema` dependency at the new version.

**PR 2: `EvalService`, the store and recovery** (runtime minor bump)
1. Add the `eval_runs` table, the `Store` methods (create, append case, finish, get, list, latest finished, sweep stale, prune), and the migrate-list entry.
2. In `run_eval_set`: keep per-case usage, add an optional per-case timeout and a stop check, and accept a list of cases to skip (for resume). All of these default off.
3. Add `eval/_service.py` with `run`, `resume`, `get_run`, `list_runs` and `list_sets`, and the boundary-test rule.
4. CLI:
   - `swarmkit eval` moves onto the service, with the legacy-file fallback for `--compare`;
   - add `eval runs`, `eval show` and `eval resume`;
   - the sweep runs on CLI start.
5. Docs: `eval-harness.md` gets a pointer to slice 3, plus the CLI reference, llms.txt and its regeneration.

**PR 3: serve routes** (runtime minor bump) — shipped in 1.268.0, see [As built: PR 3](#as-built-pr-3)
1. Add the `eval-set` kind to `ArtifactService`, with schema validation and the target check, and the `/api/evals` CRUD routes.
2. Add `server/_routes_evals.py`: run, list, get, stop and resume, the semaphore slot, the task registry, 409 on the API tier, and the startup sweep beside `_sweep_stale_jobs`.
3. Add the test that no agent-facing tool writes eval sets.
4. Docs: the serve API reference, llms.txt, and `just demo-eval-serve`.

**PR 4: portal Evals page** (`swarmkit-webui` minor bump, `[ui]` floor) — shipped, swarmkit-webui 0.30.0
1. `app/evals/` with list, review, edit, run and follow, and report.
2. vitest coverage, and screenshots of every view and action, from the repeatable capture script.

## As built: PR 2

PR 2 follows §2–§4, apart from the points below. Each one is a change from the text above, with the reason.

- **The heartbeat ticks on a timer, not per case.**
  - A running eval moves `heartbeat_at` every 30 seconds while a case is in flight. The sweep takes a `running` row whose heartbeat is older than five minutes (`HEARTBEAT_INTERVAL_S`, `STALE_AFTER_S` in `eval/_service.py`).
  - A per-case heartbeat bounded by the serve's `timeout_seconds` wouldn't work from the CLI. A CLI run has no such timeout, and the CLI doesn't know the timeout of a serve sharing its store, so its startup sweep could close a live serve eval.
  - A timer makes "stale" mean the same thing whoever is sweeping and however long a case takes.
- **`stop_requested_at` is a column**, as it is on `jobs`. A stop has to reach an eval running in another process that shares the store. The run checks the column before each case.
- **Cancellation is recorded at once.** A run cancelled mid-case, by Ctrl-C or a serve shutting down, marks its own row `interrupted` before it unwinds. Only a SIGKILL or a power cut is left to the sweep.
- **No retention pruning yet.**
  - `storage` in `workspace.schema.json` is strict, so `storage.evals.retention_days` is a workspace-schema change. It belongs in its own PR, not one about the eval service.
  - A row is one per eval run, not one per case, so growth is small. Pruning and the setting follow together.
- **The CLI commands are a group, `swarmkit eval-runs list | show | stop | resume`.** `swarmkit eval <workspace> <set>` takes positional arguments, so it can't also hold subcommands.
- **A store that won't open falls back to the workspace's local SQLite store, and says so.** It's the same principle as `swarmkit run`: an unreachable database costs the record's location, never the eval. CI gates on the exit code.
- **Exit codes.**
  - `eval` exits 1 for any run that doesn't end `succeeded`. That covers failed, stopped and errored runs.
  - It exits 2 for an unknown set, run or target. Before, only a failing case gave 1.
- **Cost counts the cases' runs only.** A judge or rubric verdict (`DecisionSkillResult`) carries no usage today, so those calls aren't in `cost_usd` yet.
- **Not built yet:** the topology-change check on resume (first open question). Resume refuses a run whose target topology is gone, but doesn't yet compare content hashes.

## As built: PR 3

PR 3 follows §5, apart from the points below.

- **`EvalService` splits admission from execution.** `begin` / `begin_resume` load and check the set, then write or claim the row. `execute` scores the cases. `run` and `resume` are the two halves back to back, as before. The route calls the first half inline, so a refusal is a status code and not a failed row, and then returns 202 and hands the row to `execute` in a task.
- **The slot is taken before the row is written.** Admission checks the semaphore and acquires it with no await in between, so two requests can't both see it free. The row is written only after that, so a 429 never leaves a `running` row. A refusal after the slot is taken, such as an unknown set, gives the slot back.
- **Shutdown cancels evals before it closes the runtime.** Left to the loop's teardown, the eval would be cancelled only after the runtime it uses had been closed. The lifespan cancels the eval tasks first, and each marks its row `interrupted`.
- **Eval-set writes validate the one file, not the workspace.** Nothing the runtime runs reads an eval set, so a write needs no workspace validation and no runtime reload. It checks the schema and unique case ids (the loader's check), that `metadata.id` matches the URL, and that `target` is a live topology. It restores the previous file on any failure, and answers 422 with a `{code, message}` the editor can show. Topology writes still answer 200 with `valid: false`. Eval sets use 422 as §5 says, and the other kinds weren't changed here.
- **A set id from a URL must be lowercase-kebab.** `load_eval_set` also takes a file path, which is right for the CLI and wrong for a request. A non-kebab id is refused (404 on reads and runs, 422 on writes) before it reaches the loader.
- **One row shape.** `EvalRunRow.summary()` and `.detail()` are what both `eval-runs list --json` and the routes return, so the CLI and HTTP shapes can't drift apart.
- **No agent write path, tested two ways.** The authoring `write_file` tool refuses `evals/`. A source scan also checks that only `server/_routes_evals.py` calls the eval-set writers. Neither MCP front door exposes an artifact write.
- **Found while demoing: [#1111](https://github.com/delivstat/swarmkit/issues/1111).** A terminal Ctrl-C signals the harness too. Its `exit -2` is returned as node output and scored as the in-flight case's answer, a moment before serve shuts down. The fix belongs in the harness node, beside #1109, not in the eval layer. The demo signals serve alone.

## As built: PR 4

PR 4 follows §6, apart from the points below.

- **One route, query views.** The portal is a static export with no dynamic segments, so the page is `/evals/` with `?set=`, `?set=&edit=1`, `?new=1` and `?run=`, like `/job/`.
- **A 422 lands on its field.** `SchemaForm` takes an optional `errors` map keyed by JSON Pointer, and threads each field's pointer down the recursion. The loader already words a schema error as `<file> @ /cases/1/expect: …`, so the pointer is read from the message. `target.unknown` goes on `/target`, and the id codes on `/metadata/id`. An error with no field (bad YAML, a duplicate case id) shows above the form. Other `SchemaForm` callers pass nothing and are unchanged.
- **A case's history is the last five runs.** The run list carries no cases, so the review fetches the five newest runs in full. A case is flagged regressed when the newest run that scored it failed it and the one before that passed it. That's the stored diff's rule applied per case, so an interrupted run that never reached a case doesn't hide its regression.
- **Following a run is a loop, not an interval.** `followEvalRun` fetches, hands over the row, and waits before the next fetch only while the row is `running`. Responses never overlap, it stops by itself, and a failed fetch (a serve restarting under the run) is shown and retried. Stop and resume restart it.
- **Runs of a deleted set stay listed**, marked "deleted set", which settles the second open question for the portal.
- **Screenshots come from a real serve**, not mocked routes: `packages/ui/demos/seed_eval_workspace.py` starts one on the fake harness and `screenshot_evals.mjs` clicks through the flow.
- **A known rough edge.** The `target` picker can't show a value that isn't a topology, so an unknown target shows an empty picker, with the error underneath naming the bad value.

## Test plan

**PR 1**
- Every existing eval set (`examples/`, test fixtures) validates.
- Invalid fixtures are rejected:
  - a missing `target`;
  - duplicate case ids;
  - a bad `metadata.id`;
  - an unknown `expect` key;
  - `min_confidence` outside [0, 1].
- Codegen drift is clean, and the runtime model and the schema agree on a round trip.

**PR 2**
- **`EvalService`:**
  - run → row `running` → cases appended in order, with the heartbeat moving forward → a terminal status with the report and diff;
  - a set that can't load → `error`, with no row left `running`;
  - the diff is taken against the previous *finished* run, never one in flight.
- **Recovery:**
  - a row with a stale heartbeat is swept to `interrupted`, and one with a fresh heartbeat is not (two instances sharing a store);
  - resume runs only the cases without results, on the same row, against the snapshot. Editing the set after the run started doesn't change what resume runs;
  - resume is refused for a `succeeded` run.
- **Kill test, modelled on `test_kill9_recovery.py`:** kill a CLI eval after case 2 of 4, check the sweep marks it `interrupted`, resume it, then assert cases 1–2 ran once, 3–4 ran once, and the cost was counted once.
- **Store, on SQLite and Postgres:** round trip, ordering and filtering, the sweep, pruning, and `storage migrate` copying `eval_runs`.
- **CLI:**
  - `test_eval_cli.py` passes unchanged, with the same output and exit codes;
  - `--compare` reads the store, falling back to a legacy file;
  - `--output` still writes the file.
- **Cost:** with a stub runtime that returns usage, `cost_usd` equals the sum.
- **Boundary:** a new `run_eval_set(` call outside the service fails the boundary test.

**PR 3**
- **CRUD:**
  - create, read, replace and delete;
  - a schema-invalid write → 422 with the previous file restored;
  - an unknown `target` → 422;
  - `read` can't write, and `admin` can.
- **Runs:**
  - 202, then polling sees cases arrive, then a terminal status;
  - 404 for an unknown set, 409 on `enqueue_only`, 429 with the semaphore full;
  - stop mid-run → `stopped` with the cases done so far, then resume → completes.
- **Capacity:** an eval holds exactly one slot, released on success, failure, stop and exception.
- **Restart:** a serve starting over a stale `running` eval row marks it `interrupted`.
- **Same service:** CLI and HTTP rows have the same shape apart from `source`, and each one's diff sees the other as its prior run.
- **No agent write path:** no MCP or agent-facing tool writes under `evals/`.

**PR 4**
- vitest for the list, review, edit form, run polling and report views, against mocked routes.
- Screenshots: list, a set's review, the edit form with a validation error, a run in progress, a finished report with a regression, and resume.

## Demo plan

`just demo-eval-serve` (in `examples/`, on mock providers, so it needs no key):
1. Start `swarmkit serve` on a workspace with a four-case eval set.
2. Over HTTP, add a case with `PUT /api/evals/{id}`, then start a run and poll it.
3. Kill the serve after two cases, then restart it. The run shows `interrupted`.
4. `POST /evals/runs/{id}/resume`. Only the remaining cases run, and the report and cost are printed.
5. Change the topology's prompt so one case breaks, reload, and run again. Print the diff showing the regression.
6. Run `swarmkit eval runs` on the CLI, which lists all of it from the same store.

PR 4's screenshots show the same flow in the portal.

## Later: parallel cases

Parallel cases belong in a later PR, once captured sets (#1105) make large sets common:
- **A set-level `concurrency` (default 1)** would run up to N cases at once and take N capacity slots, or fewer if fewer are free.
- **Reports stay ordered by case**, whatever order the cases finish in.
- **Resume is unaffected**, because it already works from "cases without results".

That field is added to the schema when the feature ships, not reserved now.

## Open questions

- **Should a resumed run re-check that its target topology is unchanged?** It could compare the topology's content hash at start with the hash at resume. A changed topology mid-eval scores two versions in one report. The options are to refuse, to warn and note it on the report, or to allow it silently. The proposal is to warn, and note it on the report.
- **Should deleting a set keep its run history?** Proposed: yes, because the runs carry their own snapshot. The portal shows them under "deleted sets".
