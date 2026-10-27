---
title: Parallel independent tasks — a shared findings board for workers in one batch
description: Run many independent tasks in parallel under one plan, let the workers share short typed findings (facts, disproved hypotheses, claims) while they run, verify each task by running a command against its diff, and report the batch's cost and per-task outcome — composed from structured delegation, harness nodes and funnels plus one new append-only findings store.
tags: [delegation, coordination, harness, governance, memory]
status: draft
tracking: "#1092"
---

# Parallel independent tasks — a shared findings board for workers in one batch

**Status:** draft (design only) — review before implementation. **Tracking:** #1092.
**Builds on:** `structured-delegation.md` (plan → execute → checkpoint), `executor-abstraction.md`
(harness nodes in worktrees), `gate-funnel.md` (validate → judge → approve), `governed-memory.md`,
`spaces-pattern.md` (`correlation_id`), `command-packs.md`. Governing frame: §8 (Separation of
Powers); tie-breaker: §7.

## The problem

Some work splits into many tasks that don't depend on each other: N tickets of the same kind, a
module-by-module migration, rebuilding a program one area at a time, the same refactor across 40
services. Today a workspace runs these either one at a time, or with a script that loops
`swarmkit run`. Neither lets the workers learn from each other while they run, and neither gives one
governed, costed view of the batch.

Microsoft's Agensh (MIT, released 2026-10-24) is the evidence that the sharing matters. Its workers
post short typed notes to one shared board (`board-service/shared_lessons.py`): `FACT`, `OBSERVED`,
`FAIL`, `CLAIM` and `PATCH_SUMMARY(files|idea|evidence)`, capped at 100 characters (300 for patch
summaries), with the full text stored behind a `board_unfold` lookup. Their worker prompt calls
`FAIL` (a hypothesis that was disproved) "the highest-value entry: it stops peers spending budget on
it". On ProgramBench Pandoc, going from 1 to 128 workers raised the pass rate from 33.9% to 50.9% in
the same six hours.

We want the sharing, not the rest of Agensh. It has no human gates, records no tokens or cost, and
its "verifier" (`board-service/verifier.py`) only rejects placeholder evidence text such as "tbd"; it
never runs the tests a worker claims to have run.

## Goal

A topology author can say "run these N independent tasks with this worker, at most K at a time,
check each one by running this command", and get:

1. **Parallel workers**, each in its own sandbox, under the existing budgets.
2. **A shared findings board for the batch.** Every worker reads its peers' findings when it starts
   and can read or search them during its run. It writes its own findings during its run, not only
   at the end.
3. **Verification by running something.** Each task's diff is checked by a command the runtime
   runs, not by the worker's say-so.
4. **One view of the batch:** per-task outcome (passed, failed or needs a person), the findings
   board, and total tokens and cost.

## Non-goals

- **No self-organising mode.** Agensh has no orchestrator: workers look at what's left and claim it.
  Here a plan (written by a coordinator agent or by the calling app) assigns tasks. A `claim`
  finding is advisory, there to avoid duplicated *investigation* inside a task, not to allocate
  tasks.
- **No mid-turn push in v1.** Agensh appends a peer's new note to whatever tool result a worker is
  waiting on (`mini-router/mcp-notice-shim.cjs`). In v1 a worker sees peers' findings at the start of
  its run and whenever it calls the read tool. Push is a later, separate decision (open question 3).
- **No human-free mode.** Gates stay. A failed verification or a task the funnel parks goes to a
  person exactly as it does today. Agensh's "there is NO human to ask" is not a configuration here.
- **Not a new sequencing layer.** The pipeline layer was removed in 1.189.0 and sequencing belongs to
  the calling app. A batch is one run of one topology. Running several batches in order is the
  app's job.
- **Not a replacement for governed memory.** Findings live for the length of a batch. Promoting a
  durable lesson into governed memory goes through the existing governed write path.

## What already exists

| Need | What exists | Where |
|---|---|---|
| Fan out N tasks | Structured delegation: the coordinator writes a plan; tasks whose `depends_on` are met run together in one batch | `design/details/structured-delegation.md` §"Task plan"; `_task_plan.py:208-217` (`get_runnable_tasks`); `_task_executor.py:136-137` (`asyncio.gather` over the batch) |
| The same worker on many tasks | A plan task names `agent`; several tasks can name the same child | `structured-delegation.md:167-171` (task schema) |
| One sandbox per task | Each harness execution provisions its own ephemeral git worktree (or container) and tears it down | `_harness_node.py:372-397` (sandbox selection); `executors/_sandbox.py:51` |
| The task's output artifact | The harness node collects the worktree diff, excluding runtime-written files | `_harness_node.py:907-922` |
| Per-task budget | `max_turns`, `max_wall_clock_minutes`, `max_cost_usd`, enforced on the event stream and cancelling the process | `executors/_budget.py:31-104` |
| Results flow up to the coordinator | After each task, an LLM call summarises it into `key_findings` on the plan | `_task_executor.py:113-125` (`_summarize_result`, `plan.mark_completed`) |
| Checks between batches | `checkpoint` and `pre_synthesis` decision-skill triggers | `_task_executor.py:165-182`, `_decision_gate.py:259-299` |
| Context injected before an agent runs | `memory-reader` at `pre_input` searches workspace memory and governed memory and prepends what it finds | `_compiler.py:307-328`; `memory/_gate.py:53-110` |
| A durable shared store | Governed memory: keyed upsert on `subject::attribute` plus an append-only change log | `governed_memory/_store.py:139-215`; `governed_memory/_models.py:26-33` |
| Writing memory mid-run (model nodes) | Calling the `governed-memory` persistence skill writes through the governed path | `_skill_executor.py:73-82, 211-231` |
| Tools for harness nodes | The governed MCP gateway re-exposes granted `mcp_tool` and `agent` skills | `_harness_node.py:226-236, 543-570`; `mcp/_gateway.py:68-90` |
| One id across runs | `correlation_id` on every job, filterable in `/jobs/history` and `/audit` | `spaces-pattern.md` §1, §3 |
| Gates on a result | Funnels: `validate` (schema, autocorrect, `cited_change`, `slice_budget`) → `judge` → `approve` | `packages/schema/schemas/funnel.schema.json`; `gate-funnel.md` |
| Running a local binary as a skill | Command packs (`implementation.type: command`) | `command-packs.md`; `_skill_executor.py:94-103` |

## The gaps

1. **No store fits a findings board.** Governed memory is the wrong shape for it (see "Where
   findings live"), and workspace memory (`memory/_store.py`) is an untyped, deletable, TF-IDF note
   store filled by an LLM extractor at `post_output`.
2. **Harness workers can't write anything mid-run.** The gateway only offers `mcp_tool` and `agent`
   skills (`_harness_node.py:229`). The `governed-memory` skill is `llm_prompt`, so a harness worker
   never sees it.
3. **`pre_input` injection is a similarity search on the task text** (`memory/_gate.py:53-110`, top
   `max_results` by relevance). A board needs "everything peers posted in this batch, newest first,
   capped", not "the five entries most similar to my instruction".
4. **No check runs a command against a diff.** Funnel `validate` has no command layer, and a
   decision skill can't be command-backed: `evaluate_skill` calls `execute_skill` without
   `command_packs` (`governance/_decision_evaluator.py:46-53`), so a `command` decision skill can't
   resolve its pack. Even if it could, the worktree is gone by the time the gate runs.
5. **Concurrency and batch cost aren't capped.** `runtime.max_concurrent_tasks` and
   `task_timeout_seconds` validate but nothing reads them (#1093). Harness-node cost goes into the
   trace but never reaches `max_cost_per_run_usd` (#1094). Both are prerequisites for running 30
   workers, and both are filed as their own fixes.

## Design

### The batch

A batch is one structured-delegation plan whose tasks are independent, run by one topology. Nothing
new is needed to express it:

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata: { name: migrate-services, version: 0.1.0 }
runtime:
  max_concurrent_tasks: 6          # enforced once #1093 lands
  task_timeout_seconds: 1800
agents:
  root:
    id: coordinator
    archetype: batch-coordinator   # writes the plan: one task per service, no depends_on
    children:
      - id: migrator
        archetype: harness-migrator  # executor.kind: harness; one worktree per task
        skills_additional: [batch-findings]
    findings:
      enabled: true                # the batch board (new, see below)
      verify: migrate-check        # a check run against every task's diff (new, see below)
```

The calling app can skip the coordinator and post the plan itself. That is the recommended path when
the task list comes from somewhere deterministic, such as a ticket query or a directory listing, so
no model has to copy it.

### Finding entries

One finding is a short typed entry. The types are Agensh's, lower-cased, because each one makes peers
act differently:

| Type | Means | What a peer should do | Content cap |
|---|---|---|---|
| `fact` | Something the worker confirmed | Use it; don't re-derive it | 200 chars |
| `observed` | Behaviour seen but not yet explained | Use it as evidence | 200 chars |
| `fail` | A hypothesis or approach that was tried and disproved | Don't try it again | 200 chars |
| `claim` | "I'm investigating X now" | Pick a different angle if you were about to do X | 200 chars, expires (default 30 min) |
| `patch_summary` | What the worker changed: `files`, `idea`, `evidence` | Reuse the idea; check for overlap | 400 chars |

Caps are double Agensh's (100/300), because SwarmKit's tasks are coarser and the cap's purpose is to
stop the injected board crowding out the task, not to force telegraphese. Each entry can carry an
optional `detail` (up to 8 KB), which is not injected and is fetched on request. That is Agensh's
`unfold`.

Stored shape (append-only, one row per write):

```python
@dataclass(frozen=True)
class Finding:
    id: int                 # monotonic per store; the cursor a worker reads "since"
    namespace: str          # "batch:<run_id>" or "space:<id>"
    type: FindingType       # fact | observed | fail | claim | patch_summary
    content: str            # capped
    detail: str | None      # not injected; read via findings_get
    task_id: str            # stamped by the runtime from the plan task, never from tool args
    agent_id: str           # stamped by the runtime
    created_at: str
    expires_at: str | None  # claims only
    supersedes: int | None  # a worker's newer patch_summary replaces its older one in reads
```

There's no update and no delete. "Supersedes" and "expired" are read-time filters, as in Agensh's
recent view (`shared_lessons.py`), and the full history stays searchable. That keeps the store
inside invariant 5: append-only from the executive side.

### Namespace

- **Default: `batch:<run_id>`.** One run is one batch, and the board dies with the batch for
  injection purposes, though rows are retained.
- **Across runs: `space:<id>`** when the run carries a `correlation_id` of the form `space:<id>`
  (`spaces-pattern.md` §1). This lets an app spread one batch over several runs (a retry of the
  failed tasks, say) and keep one board.

A worker can read and write only its own batch's namespace. The runtime stamps the namespace from the
run context. It is not a tool argument, so a worker can't write into another batch's board.

### How workers write

A bundled skill, `batch-findings` (category `coordination`), backed by `mcp_tool` against a bundled
in-process MCP server named `swarmkit-findings`. Making it an `mcp_tool` skill is what gets it to
both node kinds: model nodes call it as a tool like any other skill, and harness nodes receive it
through the governed gateway with no new mechanism (gap 2). Every call goes through
`check_mcp_permission` and is audited like any other governed tool call.

Tools:

| Tool | Arguments | Returns |
|---|---|---|
| `findings_write` | `type`, `content`, `detail?`, `files?/idea?/evidence?` (patch summary) | the entry id, or a refusal with the reason |
| `findings_read` | `since?` (an id), `types?` | the live board (expired claims and superseded patch summaries filtered out), newest first, capped at 200 entries |
| `findings_grep` | `query` (`a&b,c` = (a AND b) OR c, case-insensitive) | matching entries across the full history, capped at 100, with the total count |
| `findings_get` | `id` | one entry including `detail` |

Deterministic write checks (no LLM), refusing with a reason the worker can act on:

- **Type and cap.** Unknown types are refused. Over-cap content is refused, not truncated: a
  truncated `fail` can say the opposite of what was meant.
- **`patch_summary` must name `files` and non-placeholder `evidence`.** This reuses Agensh's
  placeholder list ("tbd", "should work", "not verified", …). It is a hygiene check, not
  verification; verification is the next section.
- **Exact duplicates** (same type, same content, same namespace) return the existing id and write
  nothing.
- **A write budget per task** (default 40). This stops a looping worker filling the board, and it is
  the same lesson as the Copilot CLI probe in #1091, where a denied tool was retried 55 times.

### How workers read

1. **Start of run.** When `findings.enabled`, the agent's `pre_input` gets a `==== batch findings
   ====` block: the live board for its namespace, newest first, `fail` entries first, cut at a token
   budget (default 2,000 tokens). This is a new branch next to `memory_pre_input`
   (`_compiler.py:307-328`). It is not similarity search, which is gap 3.
2. **During the run.** `findings_read(since=<last id>)` and `findings_grep`. The worker's prompt tells
   it to check after it forms a hypothesis and before it spends turns on one. Agensh does the same,
   minus the push.
3. **The coordinator** sees the board at `checkpoint` alongside `key_findings`, so a checkpoint
   decision skill can, for example, stop a batch whose board shows the same `fail` from five workers.

### Verification by running a command

Each task's output is its diff (`_harness_node.py:907-922`), and the worker's worktree is torn down
when the node returns. Verification therefore runs **on the diff, in a fresh sandbox**, after the
worker has finished:

1. Provision a new worktree (or container) at the same base ref.
2. Apply the task's diff. If it doesn't apply, the task fails verification with the conflict as the
   reason.
3. Run the batch's `verify` check: a `command` skill from a command pack (`command-packs.md`), with
   argv-only invocation, effects and a governed permission check. Example: `migrate-check` running
   `pytest -q tests/<service>` or `./compile.sh && ./run-golden.sh`.
4. Treat exit 0 as `pass` and anything else as `fail`, with the last 4 KB of output as the reason.
   The result is recorded as `decision.evaluated`, the same audit event decision skills use, so it
   shows in `/audit` and the run view.

A failed verification feeds back to the worker once, as a retry with the failure output, under the
same `max_retries` rule funnels use. If it fails again the task is marked failed and its diff kept
for a person. A passing task carries on to whatever funnel or approval the topology declares, so
gates stay.

Why a fresh sandbox rather than the worker's own: the check has to run on exactly what will be
merged, in an environment the worker could not have prepared. This is the property Agensh's
text-only verifier lacks.

Where it plugs in: this is a funnel `validate` layer of a new kind, `command`, since validate is
already the deterministic, no-LLM layer (`funnel-deterministic-validate.md`). Doing it as a
command-backed decision skill would also need gap 4 fixed: `command_packs` passed through
`evaluate_skill`, plus a way to hand it a sandbox. The validate-layer route is smaller and keeps
"deterministic checks live in validate".

```yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata: { id: migrate-gate }
spec:
  validate:
    command:                       # new layer
      skill: migrate-check         # a command skill
      on: diff                     # apply the node's diff to a fresh sandbox, then run
      timeout_seconds: 600
  judge: { skill: migration-review, threshold: 0.75, max_retries: 1 }   # optional
  approve: { rules: [...] }                                            # optional
```

### The batch view

No new endpoint for v1. The batch is one run, so the existing run view already shows the per-task
nodes, tokens and cost. Two additions:

- `GET /observability/runs/{id}/findings` returns the board (live view plus full history), and the
  portal's run page gets a Findings tab.
- The plan's per-task status gains `verified: pass | fail | skipped` next to the existing
  `completed | failed`.

Total cost is the run's cost once #1094 makes harness cost count toward it.

### Promoting findings to governed memory

At `pre_synthesis` an optional curator step can propose `fact` and `fail` entries as governed-memory
candidates (`subject = batch topic`, `attribute = finding hash`), through the existing governed write
path with reconcile and quarantine. Nothing is promoted automatically. This is how "service X needs
the v2 auth header" outlives the batch without the board becoming a second, ungoverned memory.

## Where findings live: a new append-only store, not governed memory

**Recommendation: a small new `findings` table on the existing persistence layer** (SQLAlchemy Core,
SQLite or Postgres, the same engine as the job and memory stores). It is not a new database and not
a new service, but it is deliberately **not** governed memory, for five reasons:

1. **Wrong identity model.** Governed memory is keyed by `subject::attribute` and upserts
   (`_models.py:26-33`, `_store.py:139-215`). Two workers independently posting "approach X fails" is
   two pieces of evidence. Reconcile would collapse them into a `reinforce`, or treat differing
   wording as a changed value.
2. **Wrong write cost.** A changed value under the same key calls the reconcile decision skill, an
   LLM judge (`_store.py:161-215`). A batch of 30 workers posting dozens of findings each would pay
   for a judge call on many writes. Findings need checks that are deterministic and cost nothing.
3. **No expiry.** Governed memory has confidence decay by type (`_decay.py`), which only ranks old
   entries lower. A `claim` needs a hard expiry so a crashed worker's claim stops misleading peers.
4. **Wrong escalation.** A conflicting value in governed memory is quarantined and parked for a
   human (`_store.py:412-440`). Contradictory findings in a batch are normal: one worker's `observed`
   is another's `fail`. Parking them would put a person in the loop of routine investigation.
5. **Wrong lifetime.** Governed memory is durable, curated knowledge. A batch board is working state
   for one batch. Keeping them apart is what lets the promotion step stay a governed, deliberate act.

The cost of a new table is a migration plus one store class. The `tables.py` and `_store.py` pattern
in `governed_memory/` and `memory/` is the template, and both backends come with it.

## API summary

- **Topology schema:** `agents.<id>.findings: { enabled: bool, verify?: <command-skill-id>,
  inject_tokens?: int, write_budget?: int, claim_ttl_minutes?: int }` on the coordinator (it applies
  to the coordinator's plan).
- **Funnel schema:** `spec.validate.command: { skill, on: diff | output, timeout_seconds }`.
- **Bundled skill:** `batch-findings` (`coordination`, `mcp_tool` → `swarmkit-findings`).
- **Runtime:** `findings/` package with `_tables.py`, `_store.py` (`append`, `live_view`, `grep`,
  `get`) and `_server.py` (the in-process MCP server; identity and namespace come from the run
  context). The `pre_input` branch lives in `_compiler.py`.
- **Serve:** `GET /observability/runs/{id}/findings`. CLI: `swarmkit findings <run-id> [--grep q]`
  for CLI ⇄ serve parity.

## Test plan

- **Store:** append-only (no update or delete path exposed); `live_view` drops expired claims and
  superseded patch summaries but `grep` still finds them; `since` cursors are monotonic under
  concurrent appends (SQLite and Postgres); `grep` boolean parsing (`a&b,c`).
- **Write checks:** unknown type refused; over-cap refused, not truncated; placeholder `evidence`
  refused; an exact duplicate returns the existing id; the write budget is enforced per task.
- **Identity:** `task_id`, `agent_id` and `namespace` come from the run context; a tool argument
  trying to set them is ignored; a worker in batch A can't read or write batch B.
- **Both node kinds:** a model node and a harness node, using the claude-code adapter on a recorded
  fixture, each write and read through the same skill. The harness path goes through the gateway and
  appears in `executor.mcp_usage`.
- **Injection:** `pre_input` puts `fail` entries first and respects the token budget; with
  `findings.enabled: false` nothing is injected.
- **Verification:** a diff that passes the command → `pass`; one that fails → retried once with the
  output, then `fail` with the diff kept; a diff that doesn't apply to the base → `fail` with the
  conflict; the check runs in a fresh sandbox (a file the worker left outside its diff isn't
  visible).
- **Concurrency:** with #1093, `max_concurrent_tasks: 3` over 9 tasks never has more than 3 running.
  With #1094, total cost across workers trips `max_cost_per_run_usd`.

## Demo plan

`just demo-parallel-tasks` runs a small, genuinely independent batch: eight toy Python modules under
`examples/parallel-tasks/`, each with one seeded bug and its own failing test file. Each task is "make
`tests/test_<module>.py` pass without editing the test".

- One coordinator-free plan posted by the demo script (8 tasks, `max_concurrent_tasks: 4`).
- A worker, with a harness adapter or a model node with file tools so the demo also runs on a cheap
  model, and `batch-findings` granted.
- Two modules share a trap: the obvious fix breaks a second test, and the right fix is in a shared
  helper. The demo shows the first worker posting a `fail` and the second worker's injected board
  containing it, with fewer turns spent. That is the board's whole point, made visible.
- `verify: pytest-module` runs `pytest -q tests/test_<module>.py` on each diff in a fresh worktree.
  One module is deliberately unfixable without editing the test, so the demo shows a verification
  failure, the retry, and the task left for a person.
- The terminal transcript prints each task's outcome, the board and total tokens and cost. A
  screenshot of the run page's Findings tab goes in the UI PR.

The run is recorded with its model, case count, git sha and host. A "with
board / without board" comparison of turns and cost on the same eight modules goes in the PR body.

## Schema-change checklist

Two additive schema changes, both optional fields, so existing artifacts are unaffected. Follow
`docs/notes/schema-change-discipline.md`: canonical JSON Schema first, regenerate the Pydantic and TS
models, add valid and invalid fixtures, bump `swarmkit-schema` (minor), floor the runtime's schema
dependency, bump the runtime, and regenerate the LLM docs.

- `topology.schema.json`: `findings` on an agent.
- `funnel.schema.json`: `validate.command`.

## PR split

1. **#1093:** enforce `max_concurrent_tasks` and `task_timeout_seconds` (runtime only).
2. **#1094:** harness cost reaches `max_cost_per_run_usd` (runtime only).
3. **Findings store + skill.** The `findings/` package, the `batch-findings` skill, the in-process MCP
   server, gateway exposure, the `pre_input` branch and the `topology.findings` schema. Unit and
   integration tests with fixtures. Demo: the board on two workers.
4. **Command verification.** The `validate.command` funnel layer (fresh sandbox + apply diff + run) and
   its schema. Tests. Demo: pass, fail, retry, kept diff.
5. **Batch view.** The findings endpoint, CLI, portal tab and plan `verified` status. Screenshots.
6. **Reference workspace + `just demo-parallel-tasks`.** The eight-module example, recorded run, and
   with/without-board comparison. This PR carries `Fixes #1092`.

PRs 3 and 4 are independent of each other; both need 1 and 2 first for a safe demo.

## Open questions

1. **In-process vs stdio findings server.** In-process (like the gateway) has the run context, so
   identity and namespace stamping are trivial. A stdio server would need them passed through env,
   which the agent can't change but which loses per-call task attribution when one process serves
   several tasks. Leaning in-process.
2. **Verification for model-node workers with no diff.** `on: output` runs the command with the
   output on stdin. Is that enough, or do output-only batches want a judge only?

   **Proposed answer:** keep `on: output` for anything a script can check. Where no script can, use
   a judge that asks a few yes/no questions and decides in code, not a judge that returns one score.
   OpenChamber's goal auditor does this (`packages/web/server/lib/session-goal/audit.js`, commit
   `6b48aec`). It works like this:
   - It reads only the objective and the worker's final message, so it can't be swayed by the
     conversation.
   - It asks three questions, each with written criteria for true and false. `all_done`: is
     every requested part reported done? `remaining`: does the worker name requested work it still
     has to do? `needs_user`: is it stopped by something only a person can provide?
   - Plain code combines the answers. `needs_user` means blocked. `all_done` with nothing
     `remaining` means complete. Anything else means continue.
   - It accepts a classifier answering with probabilities or a small model answering with JSON
     booleans; both go through the same decision code.
   - It was measured on 144 hand-labelled turns. Jev got 142 right, and both misses were on the
     safe side.
   - Its working agent is told to end every turn with a factual done / verified / remaining report,
     because that report is all the auditor sees.

   For a batch, that suggests three things:
   - The task contract requires that end-of-task report.
   - The funnel `judge` layer takes a `questions:` form beside `threshold:`, each question
     classified separately and combined by a declared rule. Each answer is then auditable on its
     own, and a cheap classifier can answer them.
   - `needs_user` maps to the task being parked for a person, not to retrying.

   OpenChamber's limit is that "done" is still the agent's own claim, so this only verifies output
   no command can check. It never replaces `on: diff`.
3. **Mid-turn push.** Agensh appends new peer entries to the next tool result. For harness nodes that
   would mean the gateway appending a "new findings since your last read" footer to every gateway
   tool result. It is cheap to build but changes tool results the model sees, so it should be
   measured (turns and cost with and without) before it ships, not assumed.
4. **Merging.** v1 leaves each task's diff separate, for a person or the app to merge. Should the
   batch offer an integration step (apply passing diffs in order to one branch, re-verify) or leave
   that to the app, as sequencing is?

   **Proposed answer:** for independent tasks, leave merging to the app in v1. The diffs touch
   separate files by construction, so a merge is a deterministic `git apply` in order, followed by
   one re-run of `verify` on the combined result. That needs no agent.

   A **different pattern** does need a merge step: N attempts at the *same* task, keeping the best.
   OpenChamber's "code fusion" (`packages/ui/src/lib/multirun/fusion.ts`, commit `6b48aec`) is a
   recipe worth adopting there:
   - Each attempt's worktree, including uncommitted and new files, is saved as a snapshot commit.
   - The merging agent gets a fresh worktree at the base commit and a list of the attempts: each
     one's final answer, change counts and snapshot commit.
   - Diffs go into its prompt only if all of them together fit in 10% of its context window.
     Otherwise it reads them with `git diff <base> <snapshot> -- <path>`, one file at a time.
   - It picks the strongest attempt as the base, ports the better fixes and tests from the others,
     doesn't commit, and ends by saying what it took from each attempt.

   In SwarmKit that would be a second topology, "N attempts → merge → verify". The merged diff goes
   through the same `validate.command` as any other task. OpenChamber's merged result is checked only
   by the model that produced it; here the merge would be one more task, verified by the command like
   the rest. It's not in this note's scope, but the snapshot-commit handoff is the part to reuse.
5. **Promotion rubric.** Which `fact` and `fail` entries are worth proposing to governed memory, and
   who decides: a curator agent, or a person picking from the Findings tab?
