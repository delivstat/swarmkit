---
title: Governed memory — a persistence skill with temporal update-in-place
description: Memory writes go through governance (deterministic dedup/shape + a reconcile decision skill) and land as an update-in-place over a canonical current-state store plus an append-only change-log — so a growing application's facts evolve on the same memory rather than piling up duplicates, and only genuinely new facts create new rows.
tags: [governance, persistence, memory, skills, knowledge-base]
status: draft
---

# Governed memory — a persistence skill with temporal update-in-place

**Scope:** `skills` (a `persistence` skill category, design §6), `governance` (§8 — decision skills +
audit), reference `knowledge-curator` topology. Additive; no change to the execution model.
**Design reference:** builds on the `persistence` skill category (§6), `decision-skills.md`,
`gate-coverage-and-comprehension-debt.md` + `funnel-deterministic-validate.md` (the validate-gate
machinery this reuses), and the Knowledge Curator pattern. Seeded by the Memoria evaluation
(`reference-memoria`), expressed in SwarmKit's own idiom — **no new database**; rides the existing
SQLite/Postgres backend (SQLAlchemy Core). §8 (Separation of Powers) is the governing frame.
**Status:** draft (design-only) — review before implementation.

## Goal

Give agents a **persistent, governed memory** where, as an application grows and its facts change,
a new observation **updates the relevant existing memory in place** — bumping, correcting, or
refining it — and creates a **new** memory only when the observation is genuinely new. Memory that
naively appends every observation degrades: duplicates pile up, stale facts compete with fresh ones
in retrieval, and contradictions accumulate silently. The fix is to make **every memory write a
governed reconcile decision**, not an insert.

Two properties are non-negotiable:

1. **Update-in-place over time.** The canonical memory for a subject evolves on one row; history is
   preserved in a change-log, not by row duplication.
2. **Governed writes.** A write can be rejected, corrected, quarantined, or escalated — memory is
   subject to the same separation-of-powers discipline as any other executive action (§8).

## Non-goals

- Not a new memory *database* — no MatrixOne / no bespoke store. Retrieval reuses what a workspace
  already wires (GBrain MCP, Qdrant, or the built-in hybrid search over Postgres/SQLite).
- Not a new *capability* primitive — memory is a `persistence` skill (§6), consistent with "skills
  are the only extension primitive."
- Not a human gate on every write. Per the gate-composition rule (funnels = hard human gate **only**;
  decision skills = the automated-governance array), routine writes are governed by **decision
  skills**; a human/curator gate is reserved for **unresolved contradictions**.
- No re-implementation of embeddings/search — the reconcile step *consumes* retrieval, it doesn't
  build an index.

## The core problem: append vs. update-in-place

The reconciliation anchor is a **stable memory key** — `(subject, attribute)` (e.g.
`(user:srijith, preferred_tool_model)`), ideally derived from the entity/relation extraction a
graph-backed store (GBrain) already does. With a stable key, a later observation about the same
`(subject, attribute)` targets the *same* memory:

```
t0: (user:srijith, preferred_tool_model) = "Kimi K2.5"      confidence 0.9
t1: observation "srijith now prefers DeepSeek for tools"
      → reconcile: same key, changed value → UPDATE in place
        value = "DeepSeek V3", supersedes prior, change logged
```

Without a stable key you can only append; with one, memory *evolves*. The default bias is
**update/reinforce, not insert** — a write creates a new memory only when reconcile returns `new`.

## Mental model: current-state store + append-only change-log

Two tables, one invariant:

- **`memory` (canonical current-state)** — one row per key: `key`, `subject`, `attribute`, `value`,
  `type` (semantic / profile / procedural / episodic / working), `confidence`, `valid_from`,
  `last_reinforced_at`, `source`, `provenance`. **Mutable** — upserted per key.
- **`memory_change_log` (append-only)** — every mutation: `memory_key`, `op`
  (`new|update|reinforce|refine|quarantine|purge`), `before`, `after`, `reason`, `decided_by`,
  `timestamp`. **Never updated or deleted.**

This is how "update-in-place" and SwarmKit's **append-only audit invariant** (§8.3, §8.7) coexist:
*the memory current-state is mutable; the record of change is append-only.* You can always
reconstruct the timeline of a fact from the log — the Memoria "git for memory" idea, delivered as
current-state-plus-audit on the DB we already run, with no CoW engine.

## The governed write path (a decision-skill pipeline, not a funnel)

`memory_write(candidate)` runs, in order — cheap-and-deterministic first, LLM only when it must:

1. **Deterministic pre-filters (the `validate` analogue — no LLM).**
   - *Shape*: candidate validates against the memory JSON Schema (subject/attribute/value/type).
   - *Exact dedup*: content-hash match on an existing memory → `reinforce` immediately (bump
     `last_reinforced_at`/confidence), done. No LLM call.
   - *Key resolution*: compute the `(subject, attribute)` key; fetch the current memory for it (and
     the top-k semantically-near memories) as reconcile context.
2. **Reconcile decision skill (LLM-as-judge, audited — only for the ambiguous remainder).** Given
   the candidate + the resolved current memory + near neighbours, it returns one verdict:
   - `new` — no sufficiently-related memory → **insert**.
   - `update` — same key, changed value → **upsert**; old value superseded, logged.
   - `reinforce` — same fact restated → bump confidence/recency; **no new row**.
   - `refine` — adds detail to an existing memory → **merge** into it.
   - `contradict` — conflicts with a high-confidence memory and cannot be auto-resolved →
     **quarantine** the candidate (write it `quarantined`, don't let it pollute retrieval) and
     **escalate**.
   Deterministic guardrails wrap the verdict: an `update`/`refine` must name the target key that was
   in its context (no inventing a target), or it degrades to `new`; structured-output governance
   (M4) validates the verdict shape before it's trusted.
3. **Human/curator gate — contradictions only.** A `contradict` (or a below-threshold-confidence
   `update` to a high-confidence memory) parks for a human/curator decision — the *one* place a hard
   gate belongs. Everything else advances automatically. This keeps the expensive human attention on
   genuine conflicts, not routine memory maintenance.

Cost discipline (per `feedback_no_wasteful_api_calls` / `project_single_vs_multi_agent`): most writes
resolve at step 1 (exact dedup / clean new key) with **zero** LLM calls; the reconcile judge fires
only when a candidate is near-but-not-identical to existing memory.

## Temporal model: recency, confidence, decay

- `valid_from` / `last_reinforced_at` timestamp each fact; `supersedes` links an updated memory to
  the change-log entry that replaced its prior value.
- **Confidence decay**: a memory's effective retrieval weight decays with time-since-last-reinforced
  (configurable half-life per `type`; `working` memory can carry a TTL). Stale facts *rank down*
  rather than being deleted — retrieval prefers fresh, reinforced memory without losing history.
- **Reinforcement**: a `reinforce` verdict resets recency and nudges confidence up — so a fact that
  keeps being observed stays strong; one that stops being observed fades in ranking.

## IAM

From the Knowledge Curator pattern (`project_knowledge_base`): the curator identity holds
`memory:write`; worker agents hold `memory:read` only and submit write *candidates* through a
coordination skill — the curator's governed write path decides. `memory:write` is an ordinary
executive scope (not a reserved human-identity scope); the human gate applies to contradictions via
the decision path, not by scope. Segregation: a memory's author cannot be the human who resolves its
contradiction (reuse the funnel's `exclude_author` semantics).

## API shape

- **Persistence skill** `governed-memory` (category `persistence`): the write/read seam an agent
  calls — `write(candidate) -> WriteOutcome{op, memory_key, escalated}` and `search(query, *, types,
  as_of?) -> [Memory]` (recency/confidence-weighted; `as_of` reads the change-log for a point-in-time
  view). Backed by a `MemoryStore` over SQLAlchemy Core (SQLite/Postgres), mirroring the existing
  backend seam.
- **Reconcile decision skill** `memory-reconcile` (category `decision`): the audited judge; a rubric
  scores `new/update/reinforce/refine/contradict`. Deterministic pre-filters live in the persistence
  skill, not the judge (keeps policy out of the compiler; judgement in the decision skill).
- **Reference topology** `knowledge-curator`: schedule-triggered; ingester proposes candidates,
  curator runs the governed write path, publisher exposes read views. Demonstrates the whole loop.
- **CLI ⇄ serve parity** (per the workspace-UI discipline): `swarmkit memory search|write|log|
  quarantine` mirrored by serve endpoints + a memory panel (current-state table, per-key timeline
  from the change-log, quarantine queue). Read-only where the audit invariant requires.
- **MCP**: the skill is exposable as an MCP server so external agents (Claude Code/Cursor) share the
  same governed memory — the same seam GBrain uses.

## Failure semantics (reuse, don't invent)

A rejected write reuses the decision-skill + validate vocabulary already in the runtime: a
shape/dedup failure is handled deterministically; a `contradict` parks on the review queue exactly
like a funnel escalation (never dropped, never silently written). The reconcile judge's low-score
path carries its reasoning as the escalation context — same shape as the gate critique.

## Test plan

- **Unit:** the reconcile classifier over crafted candidate/current pairs — new-key → `new`;
  same-key-changed-value → `update`; identical → `reinforce` (no LLM, exact-dedup path); detail-add
  → `refine`; conflicting high-confidence → `contradict` + quarantine. Deterministic guardrail:
  `update` naming an out-of-context key degrades to `new`.
- **Temporal:** confidence decay ranks a stale memory below a freshly-reinforced one; `search(...,
  as_of=t0)` reconstructs the pre-update value from the change-log; the change-log is append-only
  (no update/delete path exposed).
- **Integration:** the `knowledge-curator` loop — worker submits candidates, curator's governed
  write path updates-in-place across a simulated timeline, a contradiction escalates to the curator
  gate.
- **Governance:** `memory:read` worker cannot write; append-only audit holds; segregation on
  contradiction resolution.

## Demo plan

`just demo-governed-memory`: replay a short timeline of observations about one subject whose
preferred value changes twice and gets one duplicate restatement; show the canonical memory
**updated in place** (one row, evolving), the **append-only change-log** timeline, a duplicate
resolved as `reinforce` with **no new row**, and a contradiction **quarantined + escalated** — no
external DB, deterministic where possible.

## Open questions

1. **Memory-key derivation.** `(subject, attribute)` is clean when an entity/relation extractor
   (GBrain-style) supplies it. Without one, do we (a) require the writer to name the key, (b) derive
   it with a cheap deterministic heuristic, or (c) let the reconcile judge propose it (costs an LLM
   call on every write)? Leaning (a)+(b): named key when the writer knows it, heuristic otherwise,
   judge only to disambiguate.
2. **Decay policy home.** Per-`type` half-lives as workspace config vs. per-memory override vs. a
   governance decision skill that sets decay. Start with per-type config; revisit if it's too blunt.
3. **`refine` merge mechanics.** Structured field-merge vs. LLM-synthesised value. Prefer structured
   where the memory has fields; fall back to a synth step (audited) for free-text values.
4. **Cross-backend reconcile.** When retrieval is an external MCP (GBrain/Qdrant) rather than the
   built-in store, the change-log still lives in our DB — confirm the current-state/index split is
   coherent when the index is remote (the DB is the system of record; the MCP is a search accelerator
   over it, matching GBrain's own git-as-record model).
