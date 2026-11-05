---
title: Eval capture — recorded runs become regression cases, on the CLI and on serve
description: One CaptureService reads finished job rows and their audit events from the store and proposes an eval set — the input, the skills the run used, and the recorded output as a reference the new output is judged against. A preview by default, written through ArtifactService, from `swarmkit eval-capture` and `POST /evals/capture` alike.
tags: [eval, serve, service-layer, storage, schema]
status: accepted
---

# Eval capture

**Scope:** `packages/schema` (one additive `expect` key), `packages/runtime` (eval, persistence, server, CLI), `packages/ui` (a capture dialog, last)
**Design reference:** [[eval-on-serve]] (the baseline: `EvalService`, `eval_runs`, `/api/evals`, the Evals page), [[eval-harness]], CLAUDE.md invariant 9, `docs/notes/service-layer-discipline.md`, `docs/notes/schema-change-discipline.md`
**Status:** accepted (design review on #1114, 2026-11-05; the open questions resolved as proposed, see Decisions). Second half of #1105. #1106 (labels from gate decisions) builds on the service this adds.

## Goal

A person points at runs that went well, on the CLI or the portal, and gets an eval set that fails when a later change makes the topology stop doing what it did. Writing the cases by hand is the step people skip. Every run already records most of what a case needs.

## What the store has, and what it doesn't

The issue assumed a store-backed trace (`get_trace`). That was never built: `design/details/traces-in-the-store.md` is a draft, and traces are still files under `.swarmkit/traces/<run_id>.json` (`trace.py:186`) on whichever host ran the run. Capture can't depend on them, or it works on a laptop and silently produces thinner cases on a fleet. Everything it needs is in the store instead:

| Need | Where it is | Notes |
|---|---|---|
| input | `jobs.input` (`persistence/_tables.py:21`) | the text the entry agent got |
| output | `jobs.output` | the final answer |
| outcome | `jobs.status` | only `completed` runs are captured by default |
| which run | `jobs.topology`, `version`, `source`, `labels`, `created_at` | selection filters |
| skills used | audit `skill.executed` events with `policy_decision: allow`, by `run_id` (`langgraph_compiler/_tool_loop.py:669`, `mcp/_gateway.py:378`) | audit is a store kind; it follows `storage.runtime` |
| attachments | audit `run.attachments` (`_workspace_runtime.py:1632`) | a case can't carry files, so these runs are skipped, with a reason |

CLI runs write job rows too (`cli/_cmd_run.py:376`), so both front doors' runs can be captured. Eval case runs don't write job rows (they stay off `JobService`, [[eval-on-serve]]), so capture never captures an eval's own measurement runs.

## Non-goals

- **No hook inside the run.** Capture reads the store after the fact. The run pays nothing.
- **Not labelling.** A captured case says "keep doing what you did", not "this was right". Labels from gates and judges are #1106.
- **No `equals` on model output.** It would flap on every run. The recorded output is a *reference* for a judge, not an exact match.
- **No automatic capture.** A person selects the runs and sees the cases before anything is written. A background sampler belongs with #1106's candidate queue, behind human promotion.
- **Not traces-in-the-store.** If that lands, capture can add per-agent detail; nothing here waits for it.

## Design

### 1. A case captured from a run

```yaml
- id: run-3f9a2c1d              # "run-" + the first 8 hex of the job id: stable, kebab, unique
  input: Summarise the open returns for DC-14.
  expect:
    not_empty: true
    used_skills: [search-returns, read-order]   # from the run's allowed skill.executed events
    reference: |                                  # NEW: the recorded output
      Three returns are open for DC-14: ...
    min_confidence: 0.7
  source:                                         # NEW: where the case came from
    run_id: 3f9a2c1d-...
    topology_version: 0.4.0
    captured_at: 2026-11-04T10:00:00Z
```

- **`used_skills`** lists each distinct skill the run called and was allowed, in first-call order. A denied call isn't something the run "used". Skills that only governance invoked (decision skills, the memory reader/writer bindings) are left out. They come from the workspace, not from what the topology chose to do, and they'd fail a case on a workspace setting.
- **`reference`** is a new `expect` key (§2). The new output is judged against it.
- **`source`** is a new optional case key. A captured case records the run it came from, so a reviewer can open the original and a duplicate capture can be recognised.

### 2. Schema: `expect.reference` and `case.source` (eval-set schema minor bump)

Two additive, optional keys, so every existing set stays valid:

- **`expect.reference`** (string, 1–20,000 characters). A judge decides whether the new output gives the same result as the reference. Wording, order and formatting may differ, but facts, numbers, named entities and the conclusion may not. `min_confidence` applies as it does to `judge` and `rubric`.
- **`case.source`**: `{run_id, topology_version?, captured_at}`. It's metadata, and the runner ignores it.

**Why not `rubric`?** The rubric is placed in the judge's *system* prompt (`_workspace_runtime.py:1295`). A recorded output there is instructions the judge will follow: an output containing "rate this as a pass" grades itself. The reference goes in the *user* message between delimiters, beside the new output, and the system prompt says both blocks are data. This is a new check in the runner (`reference`, beside `judge` and `rubric`), reusing `judge_rubric`'s provider resolution and verdict parser.

**Cost.** The reference judge is one model call per case, on the authoring provider, like `rubric`. Judge calls aren't counted in an eval's `cost_usd` today ([[eval-on-serve]]); that stays true and is stated in the docs rather than fixed here.

### 3. One service, two front doors

`CaptureService(runtime, store, workspace)` in `eval/_capture.py`:

```python
def select(self, *, run_ids=(), topology=None, since=None, until=None,
           status="completed", labels=None, source=None, limit=50) -> list[JobRow]
def propose(self, jobs, *, into: str | None) -> CaptureProposal
```

- **`select`** takes explicit run ids, or a topology plus filters. Explicit ids are taken as given, even a failed run if asked for, but reported. Filters default to `completed`, newest first, capped at `limit` (at most 200). It needs one new store query, `list_jobs_filtered(...)`, beside `list_jobs`.
- **`propose`** builds the cases and returns a `CaptureProposal`, without writing anything:
  - `eval_set`: the merged `EvalSet`;
  - `added`: the case ids;
  - `skipped`: `[{run_id, reason}]`, where reasons are `attachments`, `empty-output`, `output-too-long`, `not-completed`, `duplicate`, `wrong-target` and `redacted-to-empty`;
  - `flagged`: `[{run_id, reason}]`, for runs that were taken but not as a full regression case. The one reason today is `smoke-only`: a failed run named explicitly (see Decisions);
  - `yaml`: the file as it would be written.
- **Writing** goes through `ArtifactService.put_eval_set` / `create_eval_set`, the same validated write as a portal edit: schema, unique ids, target is live, restore on failure. Capture adds no second writer under `evals/`, and the source-scan test in `test_eval_routes.py` keeps it that way.

**Into a new set or an existing one.**
- `into` names the set. If it doesn't exist, it's created with `target` set to the runs' topology. That needs all selected runs to share a topology, or the request is refused, naming the topologies.
- If it exists, cases are appended. A run whose topology isn't the set's `target` is skipped as `wrong-target`. Existing cases are never rewritten.

**Duplicates.** Inputs are compared after trimming and collapsing whitespace.
- Two selected runs with the same input become one case, from the newer run.
- A run whose input is already a case in the set, or whose `run_id` is already a case's `source`, is skipped as `duplicate`.

So capturing the same runs twice is a no-op, not a second copy.

**Front doors.**

| CLI | HTTP | Tier |
|---|---|---|
| `swarmkit eval-capture <ws> --run <id>... --into <set>` | `POST /evals/capture` `{run_ids, into, write: false}` | `read` for a preview, `admin` to write |
| `swarmkit eval-capture <ws> --topology <t> [--since 7d] [--status completed] [--label k=v] [--source cli\|serve] [--limit N] --into <set>` | `POST /evals/capture` `{topology, since, status, labels, source, limit, into, write}` | same |
| `--write` writes; without it, prints the YAML and the skipped list | `write: true` writes; the response is the proposal either way, plus `{written: true}` | |

The command is `eval-capture`, beside `eval-runs`, because `swarmkit eval` takes a workspace and a set as positionals and can't grow a subcommand.

Preview is the default on both doors. The cases are going into git and into review, and a person should see exactly what will be written: inputs and outputs are copied out of the store verbatim, and the store isn't redacted (§4).

The HTTP tier is the one route whose tier depends on the body. `_required_action` maps by path and method, so the route is mapped to `read` and checks `admin` itself when `write` is true, and a test covers both. (Two routes, `/evals/capture/preview` and `/evals/capture`, would be simpler to tier; the implementation falls back to that only if the in-handler check turns out awkward.)

### 4. Redaction

The issue said redaction goes through "the trace/audit policy". That policy, the skill's `audit.redact` JSON pointers and `audit.level`, applies to audit event *payloads* (`_workspace_runtime.py:1726`). It doesn't touch `jobs.input` or `jobs.output`, which are stored as given. So there is no existing policy that covers what capture copies, and pretending otherwise would be the dangerous version.

Proposed:

- **A capture-time redaction list**, `--redact <regex>` (repeatable) on the CLI and `redact: [regex]` on HTTP.
  - Every match in the input and the reference is replaced with `[REDACTED]` before the case is built.
  - A case whose input is entirely redacted is skipped as `redacted-to-empty`.
- **A workspace default** in the workspace schema, `evals.capture.redact: [regex]`. It's applied on every capture, with the request's list added on top. Patterns are compiled at workspace load, so a bad regex is a load error, not a capture-time surprise.
  - This is a workspace-schema change and ships in its own PR (PR 4 below), so PRs 1–3 don't wait on it.
  - Until it lands, the docs say plainly that captured cases copy stored input and output verbatim.
- **Preview by default** (§3) is the last line: nothing is written that a person hasn't been shown.

`used_skills` come from audit events whose payloads the audit policy has already redacted. Capture reads only `skill_id`, never a payload.

### 5. Portal (last PR)

- **A "Capture as eval cases" action on the Jobs page** for selected rows, and **"Capture runs"** on a set's Evals view, for a topology with filters.
- Both open the proposal: the YAML, the added cases and the skipped list with reasons. "Write" calls `POST /evals/capture` with `write: true`.
- It's the same dialog either way. Nothing is computed in the browser.

## Implementation plan

Five PRs, each reviewable against a merged baseline.

**PR 1: schema** (eval-set schema minor bump, 1.53.0)
- `expect.reference` and `case.source`, with titles and descriptions for `SchemaForm`.
- Fixtures, both valid and invalid: an empty reference, a reference over the length cap, an unknown `source` key.
- Pydantic and TS codegen, and `eval-set-schema-v1.md`.

**PR 2: the `reference` check** (runtime minor)
- The runner's `reference` check, with the reference in the user message and a data-not-instructions system prompt.
- `ExpectSpec.reference`.
- `CaseResult` check name `reference`.
- Docs: the cost note.

**PR 3: `CaptureService`, the CLI and serve** (runtime minor)
- `eval/_capture.py`, `Store.list_jobs_filtered`, `swarmkit eval-capture`, `POST /evals/capture`.
- Request-level `--redact`.
- `just demo-eval-capture`.

**PR 4: workspace default redaction** (workspace schema minor, runtime minor)
- `evals.capture.redact`, compiled at load.

**PR 5: portal** (`swarmkit-webui` minor, `[ui]` floor)
- The capture dialog from Jobs and from Evals.
- vitest, and screenshots from the real-serve capture script.

## Test plan

**PR 1**
- Every existing eval set still validates.
- Valid and invalid `reference` / `source` fixtures.
- Codegen drift is clean.

**PR 2**
- With a stub judge:
  - `reference` passes and fails on the verdict;
  - `min_confidence` applies;
  - a case with no `reference` makes no judge call.
- The reference reaches the judge in the user message and not in the system prompt (assert on the `CompletionRequest`).
- A reference containing "verdict: pass" text doesn't change what the system prompt says.

**PR 3**
- **Case shape:**
  - `input`, `not_empty` and `reference` come from the job row;
  - `used_skills` come from allowed `skill.executed` events only, in first-call order. Denied calls and governance-bound skills are excluded.
  - `source.run_id` is set.
- **Skips:**
  - attachments;
  - empty output;
  - a non-completed run under filters (taken, but reported, when named explicitly);
  - a duplicate of another selected run, of an existing case's input, or of an existing case's `source.run_id`;
  - `wrong-target` on an existing set;
  - `redacted-to-empty`.
- **Idempotent:** capturing the same runs twice writes nothing the second time.
- **Both doors give the same answer:** CLI and `POST /evals/capture` produce the same YAML from the same store.
- **Preview writes nothing.** With `write`, the file is written through `ArtifactService`: an invalid result is a 422 and the previous file is kept.
- **Tiers:** a preview is `read`; a write needs `admin`, and `read` is refused.
- **Redaction:** a `--redact` pattern never appears in the written file, and a preview shows `[REDACTED]`.
- **Round trip:** captured from three runs, the set passes when run against the unchanged topology (stub provider and stub judge). After the topology's prompt changes so one answer differs, that case fails.
- **The store query** works on SQLite and Postgres: filters, ordering and the limit cap.
- **Boundary:** the source scan still finds one eval-set writer site.

**PR 4**
- A workspace pattern is applied on every capture, and the request's patterns are added to it.
- A bad regex is a load error naming the pattern.

**PR 5**
- vitest for the dialog against mocked routes: proposal, skipped reasons, write, and a 422.
- Screenshots of the Jobs action, the proposal, the written set and its first run.

## Demo plan

`just demo-eval-capture`, on the fake harness and a stub judge, so it needs no key:
1. Start `swarmkit serve`, and run a topology three times over HTTP with different inputs. Run one of them a second time, to show the duplicate collapse.
2. `POST /evals/capture` as a preview. Print the YAML, then the skipped list (one `duplicate`).
3. Capture again with `write: true` and `redact: ["DC-\\d+"]`, and show that the file has `[REDACTED]`.
4. Run the new set (`POST /evals/{id}/run`). It passes 3/3.
5. Change the fake harness so one answer differs, then run again. The diff names the case as regressed.
6. `swarmkit eval-capture --run <id>` on the CLI against the same store gives the same YAML.

## Decisions

Resolved in review on #1114, as proposed:

- **Failed runs.** Under filters, a failed run is never taken (`not-completed`). Named explicitly with `--run`, it's taken as a smoke case: the input only, with `not_empty` dropped and no `reference`, so it only re-runs the input. It's listed under `flagged` as `smoke-only`, so the preview says plainly that it isn't a regression baseline.
- **Reference length.** A recorded output over 20,000 characters (the schema's `reference` cap) is skipped as `output-too-long`. It's never truncated, because judging against half an answer is worse than no case.
- **`used_skills`.** On by default. `--no-used-skills` on the CLI and `used_skills: false` on HTTP drop it, for a topology that legitimately varies which skills it calls. The reviewer sees it in the preview either way.
- **Route tier.** One route, `POST /evals/capture`, mapped to `read`; the handler requires `admin` when `write` is true (§3).
