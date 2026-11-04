# Eval set

An **eval set** (`kind: EvalSet`) is the list of cases a topology is scored against. Each case runs the target topology on an input and checks the output. The case passes when every check set on it passes. `swarmkit eval` runs a set and exits 1 if any case fails, so CI can gate on it.

An eval set is the **test** step of growth through authoring (design §12): find a gap, author a fix, *test* it, publish it. The schema is `eval-set.schema.json` in `packages/schema`, and the runtime validates every set against it before running. The design record is [`design/details/eval-set-schema-v1.md`](https://github.com/delivstat/swarmkit/blob/main/design/details/eval-set-schema-v1.md), and how the harness scores cases is in [Eval harness](../design-notes/eval-harness.md).

## Where it lives

Eval sets are YAML files under `evals/` in the workspace. `swarmkit eval <workspace> <ref>` takes the set's `metadata.id`, its filename stem, or a path to a file anywhere:

```bash
swarmkit eval ./workspace greeting-evals            # by id, under ./workspace/evals/
swarmkit eval ./workspace ./candidate-evals.yaml    # by path
swarmkit eval ./workspace greeting-evals --compare  # and diff against the previous run
```

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: support-triage-evals          # lowercase-kebab; what `swarmkit eval` names
  name: Support triage              # optional display name
  description: Routes each ticket to the right queue and never promises a refund.
target: support-triage@1.2.0        # the topology every case runs; `@x.y.z` pins one version
cases:
  - id: billing-goes-to-billing     # lowercase-kebab, unique within the set
    input: I was charged twice for my subscription this month.
    expect:
      contains: [billing]
      not_contains: [refund approved]
      used_skills: [classify-ticket]
      judge: triage-quality         # a decision skill in the workspace
      min_confidence: 0.7
  - id: outage-is-urgent
    input: Nothing loads and the status page is down.
    expect:
      regex: "(?i)urgent|p1"
      rubric: The reply treats this as an outage and escalates it.
provenance:                         # optional
  authored_by: human
  version: 1.0.0
```

## Fields

| Field | Required | What it does |
|---|---|---|
| `metadata.id` | yes | The set's id, in lowercase kebab. |
| `metadata.name`, `metadata.description` | no | Shown to the person reviewing the set. |
| `target` | yes | The topology the cases run against. A bare name runs the workspace's current version. `name@x.y.z` pins one version, for example a canary candidate. Cases are never routed through a canary split, so a report always scores one version. |
| `cases` | yes, at least one | The cases, run in order. |
| `cases[].id` | yes | Lowercase kebab, **unique within the set**. Reports and `--compare` are keyed by case id, so keep a case's id when you edit it. |
| `cases[].input` | yes | What the topology is run on: the same text a caller sends to `POST /run`. Must not be empty. |
| `cases[].expect` | no | The checks. Empty or absent makes the case a smoke test that passes when the run completes. |
| `provenance` | no | The shared artifact provenance block. |

## Checks (`expect`)

Deterministic checks are free. `judge` and `rubric` each make one model call.

| Check | Passes when |
|---|---|
| `contains: [..]` | every string appears in the output (case-insensitive) |
| `not_contains: [..]` | no string appears in the output (case-insensitive) |
| `regex: ".."` | the Python regular expression matches somewhere in the output (`re.search`) |
| `equals: ".."` | the whole output, trimmed, equals this. Model output varies, so use it only for deterministic topologies |
| `not_empty: true` | the output contains something other than whitespace |
| `used_skills: [..]` | the run invoked every listed skill, read from its events |
| `judge: <skill-id>` | the decision skill returns `pass` at `min_confidence` or above |
| `rubric: ".."` | the workspace's judge model scores the output `pass` against this rubric at `min_confidence` or above |
| `min_confidence` | the threshold for `judge` and `rubric`, from 0 to 1. The default is 0.5 |

## Validation

A set is checked against the schema before anything runs, and an error names the field:

```text
error: ./set.yaml @ /cases/0/expect: Additional properties are not allowed ('contain' was unexpected)
```

Unknown keys are refused at every level, so a typo can't silently turn a check off. Two rules are checked by the runtime, because JSON Schema can't express them:

- case ids are unique within the set;
- the target names a topology in the workspace. This is checked when the set is run.

Since runtime 1.266.0, a set with an unknown key, no cases, a non-kebab id, an empty input or a duplicate case id is refused. Before that, those were ignored.

## Reports

Each run prints a per-case PASS/FAIL with the failing checks, a pass rate and the run's id. The run is recorded in the workspace's runtime store (SQLite or Postgres, as `storage:` says). `--output PATH` also writes the JSON report to a file. `--compare` diffs the run against the previous finished run of the same set, listing cases that regressed, were fixed or are new. When the store has no earlier run, it falls back to the newest report file under `.swarmkit/eval-results/`, where runtimes before 1.267.0 wrote reports.

## Eval runs

```bash
swarmkit eval-runs list ./workspace [--set greeting-evals]   # newest first, CLI and serve alike
swarmkit eval-runs show ./workspace <eval-run-id>            # every case, failing checks, diff, cost
swarmkit eval-runs stop ./workspace <eval-run-id>            # stop after the current case
swarmkit eval-runs resume ./workspace <eval-run-id>          # continue a stopped or interrupted run
```

- **What a run records:**
  - the set as it stood when the run started;
  - each case's result, written as soon as the case is scored;
  - the cost, summed from each case's run. Judge and rubric calls aren't counted yet;
  - the diff against the previous finished run.

  Case runs get no job rows: an eval is measurement, so it doesn't appear in job history or job usage.
- **A process that dies mid-eval** leaves its run `running`. A running eval moves a heartbeat every 30 seconds. Once that heartbeat is five minutes old, the next `swarmkit eval` or `eval-runs` command marks the run `interrupted`. Ctrl-C marks it `interrupted` at once.
- **Resume** runs only the cases that have no result yet, on the same run, against the set as it was run. Editing the file afterwards doesn't change what a resume runs. Only an `interrupted` or `stopped` run can be resumed, and only while its target topology still exists. Nothing resumes automatically.
- **Exit codes:** `eval` and `eval-runs resume` exit 0 when every case passes. They exit 1 when a case fails, or when the run stopped or errored. They exit 2 for an unknown set, run or target.
- `--case-timeout SECONDS` fails a case that runs too long, and the eval carries on.

## On serve

`swarmkit serve` runs the same evals through the same service, so a run started over HTTP shows up in `swarmkit eval-runs list`, and each front door's `--compare` (or diff) sees the other's runs.

**Sets** are an artifact kind under `/api/*`. Reads are the `read` tier and writes `admin`:

| Method + path | What it does |
|---|---|
| `GET /api/evals` | every valid set: id, name, description, target, case count, last run (status, pass rate, dates) |
| `GET /api/evals/{id}` | one set, parsed |
| `GET /api/evals/{id}/yaml` | its YAML, as written (found even when it no longer validates, so it can be fixed) |
| `POST /api/evals` | create from `{"yaml": ...}`; the file is `evals/<metadata.id>.yaml`; 409 if the id exists |
| `PUT /api/evals/{id}` | replace (or create) from `{"yaml": ...}` |
| `DELETE /api/evals/{id}` | remove the file; its run history stays |

A write is checked against the schema, and its `metadata.id` must match the URL. The `target` must be a topology in the workspace, and the id must be lowercase-kebab. A write that fails any check answers 422 with `{"valid": false, "errors": [{"code", "message"}]}`, and the previous file stays as it was. The codes are `schema`, `target.unknown`, `id.mismatch`, `id.invalid`, `missing.id` and `yaml.parse`.

No agent-facing tool writes eval sets. An agent that could edit the cases it's measured against could pass any test, so the authoring swarm's `write_file` tool refuses `evals/`, the same way `topologies:modify` is reserved for people.

**Runs:**

| Method + path | Tier | What it does |
|---|---|---|
| `POST /evals/{id}/run` | run | start: 202 with `{eval_run_id, status: "running", eval_set_id}` |
| `GET /evals/runs?set=&limit=` | read | recent runs, newest first, without their cases |
| `GET /evals/runs/{run_id}` | read | the run: the set as run, cases so far, report, diff, cost |
| `POST /evals/runs/{run_id}/stop` | run | stop after the current case, in whichever process runs it (409 if it isn't running) |
| `POST /evals/runs/{run_id}/resume` | run | continue an `interrupted` or `stopped` run: 202, then poll |

- **Capacity.** An eval takes one slot of the serve's concurrency limit (`server.max_concurrent`) for its whole run, and is refused with 429 when no slot is free. Cases run one at a time inside that slot, each bounded by `server.timeout_seconds`. A case that runs over fails with `timeout`, and the eval carries on.
- **Refusals.**
  - An unknown set or run is 404.
  - A set that doesn't validate, or whose target is gone, is 422 on `run`, and no run is recorded.
  - A finished run, or one whose target is gone, is 409 on `resume`.
  - The API tier (`serve --role api`) answers 409 to `run` and `resume`, because it executes no work.
- **Restarts.** At startup, serve marks eval runs whose heartbeat is five minutes stale `interrupted`, beside the job sweep. A run with a fresh heartbeat belongs to another live instance sharing the store, and is left alone. A serve that shuts down cleanly marks its running evals `interrupted` itself, before it closes the runtime.
- **A known gap.** A Ctrl-C in a terminal signals the harness subprocess too. The harness reports `exit -2`, and that's scored as the in-flight case's answer before serve shuts down ([#1111](https://github.com/delivstat/swarmkit/issues/1111)). Signal the serve process alone to stop it cleanly.

`just demo-eval-serve` runs the whole flow on a fake harness: edit, run, interrupt, restart, resume, regress and diff.

## In the portal

The portal's **Evals** page (swarmkit-webui 0.30.0+) reads the same routes:

- **The list** shows every set with its target, case count, and its last run's status, pass rate and date, plus recent runs. A run whose set has since been deleted stays listed, marked "deleted set".
- **A set** (`/evals/?set=<id>`) shows each case's input and expectations against its last five runs. A case that passed before and fails now is marked "regressed".
- **Edit** saves through `PUT /api/evals/{id}`, in a form generated from `eval-set.schema.json` or as YAML. A 422 is shown on the field it names, and the file on disk is unchanged.
- **Run** starts a run and opens it (`/evals/?run=<id>`). Cases appear as they're scored. "Stop after this case" and "Resume" call the stop and resume routes. The report shows each case's checks and output, the cost, and the diff against the previous finished run.

