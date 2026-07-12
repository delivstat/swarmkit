---
status: accepted
---

# Executor trust-accrual → allowlist changeset (RFC §6.2.3 / decision 6, "P3.5")

Relay (shipped) makes a human approve an out-of-grant capability mid-run — every time. Trust-accrual
turns that repetition into a **permanent, reviewed grant**: when the same `(archetype, capability)`
is approved enough times with no denials, the system **proposes a changeset** adding it to the
archetype's allowlist. The operator approves once; future runs never ask. Runtime relaying is the
evidence-gathering phase; the allowlist is where trust is recorded reviewably.

## The rule (decision 6)

- Every relayed decision is recorded against the `(archetype, capability)` pair.
- **N consecutive approvals, no denials** ⇒ propose the allowlist changeset. Default **N = 5**,
  operator-tunable per workspace.
- **A single denial is a signal, not noise:** it **resets the counter to 0 AND blocks** future
  proposals for that pair until the operator **manually clears** the block. So one "no" stops the
  system nagging to auto-grant something a human deliberately refused.
- The proposal never widens anything on its own — the operator approves it. A mature archetype can
  then flip `on_unanswerable: deny`.

## Where it hooks

The relay orchestrator (`langgraph_compiler/_relay.py::resolve_relay`) already produces a decision
(`granted` + responder). It's the one place every approval/denial flows through — so the accrual
record + threshold check live right after the decision, keyed by the agent's
`source_archetype` + the capability. No new interception point.

## Parts

1. **Accrual store** (per workspace, `.swarmkit/trust-accrual.json`): `(archetype, capability) →
   {approvals, blocked, proposed}`. `record(archetype, capability, granted)`:
   - granted ⇒ `approvals += 1`; at the threshold (and not blocked/proposed) ⇒ a **proposal**.
   - denied ⇒ `approvals = 0`, `blocked = True` (until manually cleared).
   Pure + file-backed, unit-testable without a harness.
2. **Hook in `resolve_relay`**: after `_finish`, call `record(...)`; on a new proposal, emit a
   `trust.changeset_proposed` **audit event** (archetype, capability, count) and persist a proposal
   record. Scoped to human identity — the proposal is a *suggestion*, never an auto-grant
   (invariant #6).
3. **CLI `swarmkit trust`**: `list` (pending proposals + counts), `apply <archetype> <capability>`
   (add the capability to the archetype's `executor.config.allowed_tools` — the reviewed grant),
   `clear <archetype> <capability>` (lift a denial block). Applying edits the archetype through the
   normal authoring surface; it is a human action.

## Never auto-widen

The store only *proposes*. Nothing is added to an allowlist without `swarmkit trust apply` (a human
action). This is the same human-only-scope rule as the launch gate (invariant #6): the evidence is
gathered automatically; the grant is always human-recorded.

## PR slices

1. **Accrual store + relay hook + audit** — record decisions, threshold → proposal event, reset +
   block on denial. Config: `trust_accrual.threshold` (default 5). Unit-tested (store semantics +
   the hook via the existing relay tests).
2. **CLI `swarmkit trust list | apply | clear`** — surface proposals, apply the allowlist changeset,
   clear a block. Applying amends `executor.config.allowed_tools` on the archetype.

## Deferred

The fleet-UI/serve-UI surface for trust proposals (they can reuse the `/review` + proposal patterns
later); auto-flip to `on_unanswerable: deny` once an archetype is mature (operator does it by hand).

## Acceptance

- After N approvals of one `(archetype, capability)` with no denials, a `trust.changeset_proposed`
  audit event + a pending proposal appear; earlier than N, none (RFC #10).
- A single denial resets the count and blocks the pair; no proposal is made until `trust clear`.
- `trust apply` adds the capability to the archetype's allowlist; no grant widens without it.
