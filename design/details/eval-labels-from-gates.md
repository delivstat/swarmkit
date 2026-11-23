---
title: Eval labels from gates — judgements SwarmKit already records become reviewable eval candidates
description: A human gate's approval or rejection, and a governance judge's failed verdict, are labels on a run that already exist. Candidates are derived from the store and the audit log when they are listed — no background job, nothing copied — and a person promotes one into an eval set through the same capture write. Positive cases carry the approved answer as a reference; negative cases carry the rejecting human's comment as a rubric, or the failed decision skill as a judge.
tags: [eval, governance, serve, service-layer]
status: proposed
---

# Eval labels from gates

**Scope:** `packages/runtime` (eval, persistence, server, CLI) and `packages/schema` (one additive `case.source` key). A portal review queue comes later.
**Design reference:** [[eval-capture]] (the `CaptureService` and the validated write this reuses), [[human-decision-comments]], [[gate-funnel]], §12 (growth through authoring: the runtime proposes, a person approves), CLAUDE.md invariants 6 and 9.
**Status:** proposed. Fixes #1106. Builds on #1105 (eval capture, PR 2 in #1129).

## Goal

Capture (#1105) turns a run into a regression baseline: *keep doing what it did*. A baseline can't say whether what it did was **right**. SwarmKit already records who judged a run and what they decided:

- **A human gate approving the output** is a person accepting it.
- **A human gate rejecting it, or asking for changes**, is a person refusing it, often with a comment saying why.
- **A governance decision skill returning `fail`** is the workspace's own check refusing an output.

Those are labels. They were made by the people and checks the topology answers to, at no extra cost. This note turns them into **eval candidates** that a person reviews and promotes into an eval set. Accepted outputs become positive cases, and refused ones become negative cases.

## Non-goals

- **No automatic promotion.** A candidate is a proposal. Adding it to an eval set is a human action, as every write under `evals/` is (§12).
- **No labelling UI beyond accept or dismiss.** The label is the gate's decision; a reviewer only decides whether it makes a good case.
- **No new judge.** Negative cases reuse checks that already exist: a rubric written by a human, or the decision skill that refused the output.
- **No fleet aggregation in v1** (see Open questions).

## What exists

| Signal | Where it is recorded | What it carries |
|---|---|---|
| Gate approved | audit `approval.gate_resolved`, `status: approved` | `gate_id` (`<job id>:<agent id>`), `approvers` |
| Gate rejected | audit `approval.gate_resolved`, `status: rejected` | the same fields |
| A role's decision, with its comment | audit `approval.role_task_resolved` | `outcome`, `comment`, `identity`, `role` |
| Changes requested | audit `approval.changes_requested` | `comment`, `round` |
| Governance judge failed | audit `decision.evaluated` | `verdict`, `trigger`, `flagged_items` |
| The run itself | `jobs` row | input, output, status |

Capture already reads job rows and audit events from the store. `CaptureService.write` already writes a proposal through `ArtifactService`, the validated eval-set write.

## Design

### 1. Candidates are derived, not collected

The issue proposed a background job that writes candidates after each run. Here they're instead **derived when listed**: `EvalCandidateService.list()` queries the audit log for the signal events in a time window, joins each to its job row, and builds the candidate. Only the reviewer's decision is stored, in one small table:

```
eval_candidate_decisions(run_id, signal, decision: promoted|dismissed, set_id, by, at)
```

**Why derive them:**
- **Nothing runs on the run's path, so a run can't be slowed down by it**, which was the issue's own requirement.
- **There's no copy of inputs and outputs** to keep consistent, redact or expire. Candidates disappear with the job rows and the audit retention, with no separate retention policy.
- **There's no job to monitor** when it falls behind.

**The cost:** listing does a bounded query (a window, defaulting to 14 days, and at most 500 signal events). That's the same order as `swarmkit logs`.

### 2. From signal to candidate

| Signal | Candidate | Its `expect` block |
|---|---|---|
| Gate **approved** | positive | as capture builds it: `not_empty`, `used_skills`, `reference` (the approved answer), `min_confidence: 0.7` |
| Gate **rejected** or **changes requested**, **with** a comment | negative | `rubric:` the comment(s), each prefixed with the reviewer's role, e.g. "The answer must not do what the {role} rejected: {comment}" |
| Gate **rejected without** a comment | **not a candidate** | (see Open questions) |
| Governance judge **failed** | negative | `judge: <the decision skill id>`: the new output must pass the check the old one failed |

**A rejecting human's comment can go in `rubric`. A recorded output can't.** The rubric goes in the judge's system prompt. The comment was written by an authenticated human with an approval scope, so it's trusted text, while a recorded output is text a model wrote (eval-capture §2). Model-written text never reaches the rubric: the failed judge's `reasoning` isn't used, and its skill id is used instead.

**When a run has several signals**, the most recent decision on its gate wins. A run that was approved after a round of changes is a positive case.

**Each candidate records where it came from** in `source`. This adds one key to `case.source`, `signal: capture | gate-approved | gate-rejected | changes-requested | judge-failed`. It's additive, with schema 1.54.0, and existing captured cases are unchanged.

### 3. Review and promotion

- **`list`** returns candidates newest first, each with the signal, the run's input, the proposed case and the decision comment. Runs already promoted or dismissed are left out.
- **Filters:** `--topology`, `--signal`, `--since`.
- **Promote:** `promote(run_id, into=<set>)` builds the case and writes it through `CaptureService.write`. That's the same validated write, the same duplicate rules (an input or a run already in the set is a duplicate), and the same redaction (eval-capture §4, and the workspace default from capture PR 3). Then it records the decision.
- **Dismiss:** `dismiss(run_id)` records only the decision, so the candidate stops appearing.
- **Promotion is a human action.** On serve it's under `/api/`, so it needs `admin`. On the CLI it's an explicit command. Like every eval-set write, an agent-facing door can't reach it (`test_eval_routes.py`'s writer scan is extended to cover it).

### 4. Configuration (opt-in)

```yaml
# workspace.yaml
evals:
  candidates:
    enabled: true
    signals: [gate-approved, gate-rejected, changes-requested, judge-failed]   # default: all four
    sample_rate: 1.0        # keep this fraction of signals; decided by hashing the run id, so it is stable
    max_per_day: 50         # at most this many candidates listed per day of runs
```

- **Off by default.** With `enabled: false`, `list` returns nothing, and the routes answer 404 with a hint pointing at the setting.
- **`sample_rate` is deterministic.** A hash of the run id decides it, so the same run is always in or always out, and paging is stable.
- **Duplicates are grouped.** Inputs that match after normalising whitespace form one candidate. It's the newest run, with a count.

### 5. Front doors, all through `EvalCandidateService`

| CLI | HTTP | Tier |
|---|---|---|
| `swarmkit eval-candidates list <ws> [--topology] [--signal] [--since]` | `GET /evals/candidates` | `read` |
| `swarmkit eval-candidates promote <ws> <run-id> --into <set>` | `POST /api/evals/candidates/{run_id}/promote` `{into}` | `admin` |
| `swarmkit eval-candidates dismiss <ws> <run-id>` | `POST /api/evals/candidates/{run_id}/dismiss` | `admin` |

The command is `eval-candidates` rather than `eval candidates`, for the same reason `eval-capture` is: `swarmkit eval` takes positional arguments and can't grow a subcommand. The portal's review queue is a later PR, on the Evals page beside the capture dialog.

## Implementation plan

1. **Schema** (schema 1.54.0): `case.source.signal`, an additive enum.
2. **`EvalCandidateService`** (runtime minor):
   - derivation from the audit log and job rows;
   - the `eval_candidate_decisions` table, on SQLite and Postgres, copied by `storage migrate`;
   - the `evals.candidates` workspace config;
   - the CLI and serve routes;
   - `just demo-eval-candidates`.
3. **Portal:** the review queue (webui minor, with the `[ui]` floor raised to match).

## Test plan

- **Signals:**
  - an approved gate gives a positive candidate whose case matches capture's;
  - a rejected gate with a comment gives a negative candidate whose rubric quotes the comment and the role;
  - a rejected gate without a comment gives no candidate;
  - a failed `decision.evaluated` gives a negative case with `judge: <skill>`;
  - a run approved after changes were requested is positive;
  - a run with no signal is never listed.
- **Trust boundary:** neither a failed judge's `reasoning` nor a recorded output ever appears in a `rubric`. Asserted on the proposed case.
- **Configuration:**
  - `enabled: false` lists nothing;
  - a signal type left out is ignored;
  - `sample_rate` is stable across calls and pages;
  - `max_per_day` is respected;
  - duplicates are grouped, with a count.
- **Review:**
  - promoting goes through `CaptureService.write`; the set validates, and a duplicate is refused;
  - a promoted or dismissed run is no longer listed;
  - promoting is `admin` on serve, and the writer scan still finds no agent-facing door.
- **Doors:** the CLI and serve list the same candidates and make the same eval-set change from the same store.
- **No run-path cost:** no code path inside a run writes candidates. Asserted by a source scan, the same shape as the writer scan.
- **Storage:** the decisions table works on SQLite and Postgres, and `storage migrate` copies it.

## Demo plan

`just demo-eval-candidates`: serve, a topology whose node has a human gate, a fake harness, and a role registry with two users.
1. Run three tickets. Approve one. Reject one, with the comment "never promise a refund before billing confirms". Leave the third unresolved.
2. `GET /evals/candidates` lists two: one positive and one negative, whose rubric quotes the comment.
3. Promote both into `triage-labelled`, and show the file.
4. Run the set. The positive case passes. The negative case fails, because the topology still promises the refund.
5. Change the topology's behaviour and run again. Both pass.

## Open questions

- **A rejected gate with no comment.** Proposed: not a candidate. Without the reason, a negative case can only say "don't say exactly this", which tests the wording, not the mistake. If the schema later gains an `expect.not_reference` check (the judge decides whether the new output gives the *same* answer as a known-bad one), a comment-less rejection could become a case. That's a schema addition with its own trade-offs, left for later.
- **The fleet panel.** Proposed: not in v1. Candidates come from one instance's store. A fleet view would aggregate per-instance listings through the control plane's existing per-instance proxy, without a shared store.
- **Scoring candidates before review**, so reviewers only see cases that would fail today. Proposed: not by default. Scoring costs a run and a judge call for every candidate, and a candidate that passes today is still a useful guard. Offer it as `list --score`, which runs each candidate once against the current topology version and marks the ones that fail.
