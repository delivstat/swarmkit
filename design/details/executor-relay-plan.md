---
status: draft
---

# Executor interactive tier — `relay` (mid-run permission approvals)

Starts the interactive tier deferred from P2/P3 (RFC `executor-abstraction.md` §6.2). Today a harness
node is **deny / abort only**: a request outside the launch grant is refused in place or terminates
`needs_approval` — never hangs, but never *asks*. `relay` adds the third option: the harness **pauses**,
the request lands in a human approval inbox as an AGT gate, and on a decision the run **continues** —
with the approval scoped to that single action, not a widening of the grant.

This is the RFC's stated **Tier-1** capability: feeding a decision back into a running harness needs
**bidirectional session control**, which a fire-and-forget subprocess+JSONL map (the declarative
engine) cannot express. So relay is a *narrow, declared* per-harness seam on top of a generic core —
not a return to harness-specific everything.

## What relay must do (§6.2)

1. The harness surfaces a permission request ("may I run `Bash(npm test)`?").
2. The node **interrupts and checkpoints** — session held open (short wait) or parked via resume
   token (long wait).
3. The request enters the **approval inbox** as a gate (reuse the existing `ReviewQueue`).
4. A responder decides — **policy first** (an auto-approve from the trust allowlist), else a human.
5. The decision is **fed back** (`exec.approval_response`) and the run continues.
6. The approval is **scoped to that single action** — it does not widen the run's grant.
7. It **never hangs**: a bounded wait falls back to `abort`; the idle/budget guards still apply.

## The three hard problems (and where each is solved)

| Problem | Solved by | Tier |
| --- | --- | --- |
| **Surface** the request (not silently deny) | adapter `event_map` maps the harness's permission signal → `exec.approval_requested` | declarative (data) |
| **Feed back** the decision into a live session | a per-harness **interaction driver** (bidirectional protocol) | code (Tier-1) |
| **Longevity** across a long human wait | park via resume token + re-launch with expanded grant | code (Tier-1), driver-specific |

The inbox, policy consult, scoping, audit, never-hang, and checkpoint orchestration are **generic
core**. Only the feed-back + longevity are per-harness, behind the driver seam.

## Architecture: generic core + `InteractionDriver` seam

```
harness stream ──▶ AdapterInterpreter ──▶ exec.approval_requested
                                              │
                              ┌───────────────▼────────────────┐
                              │  relay orchestrator (core)      │
                              │  1. policy consult (auto?)      │  ── GovernanceProvider
                              │  2. else inbox gate + wait      │  ── ReviewQueue + checkpointer
                              │  3. bounded wait → else abort   │
                              │  4. audit (responder, scope)    │
                              └───────────────┬────────────────┘
                                              │ decision
                              ┌───────────────▼────────────────┐
                              │  InteractionDriver (per-harness)│  ── feeds the decision back
                              │   .supports_relay               │     (hold-live or park-resume)
                              │   .grant(capability) / .deny()  │
                              └─────────────────────────────────┘
```

- **Adapter declares** its interaction capability (data): an `interaction` block —
  `on_unanswerable: relay` plus how a permission request appears (already expressible as an
  `event_map` rule emitting `approval_requested`), and which driver mechanism it supports
  (`hold-stream` | `park-resume`). No driver declared ⇒ `relay` is not available ⇒ falls back to
  `abort` (never-hang preserved).
- **Core** owns everything vendor-neutral. **Driver** is a small Tier-1 class selected by the
  adapter's declared mechanism.

## The drivers (grounded in the real `claude` binary)

Claude Code exposes both mechanisms relay needs:

- **`hold-stream`** — `claude -p --input-format stream-json --output-format stream-json`
  (realtime streaming input + `--replay-user-messages`). The session stays alive; a permission
  request arrives as a control event and the decision is written back over stdin. Right for **short**
  waits (policy auto-approve, or an operator responding in seconds–minutes).
- **`park-resume`** — kill the process, checkpoint the `session_id`, and on approval re-launch with
  `--resume <id> --allowedTools <capability>` so the resumed session may take the action. Right for
  **long** waits (hours/days) where holding a subprocess is untenable.

A harness with neither stays `deny | abort`. opencode's observed deny-then-continue is *not* relay —
it doesn't pause — so opencode remains abort until/unless it grows a pause-and-ask mode.

## Governance (the point of the feature)

- **Policy first (§6.2.2):** before any human sees it, the orchestrator asks the `GovernanceProvider`
  whether `(archetype, capability)` is already allowed (the trust allowlist). Auto-approve → no human
  interruption. This is the seam P3.5 trust-accrual later feeds.
- **Scoped single action:** an approval authorizes exactly the requested action, never widens the
  run's grant. `park-resume`'s `--allowedTools` grant is per-resumed-run and not persisted.
- **Human-only, audited:** the inbox decision is a human (or policy) act; every `approval_requested`
  / `approval_response` is audited with responder identity + scope (invariant #5). No agent can
  self-approve.
- **Never-hang regression intact:** relay has a bounded `max_approval_wait`; on expiry it degrades to
  `abort` with `exec.result{status: needs_approval}` — the headless-hang failure mode stays closed.

## PR slices (dependency-ordered)

1. **Approval events + `interaction` schema + relay resolution.** `exec.approval_requested` /
   `exec.approval_response` already exist (P2 vocabulary). Add the adapter `interaction` block to the
   schema (`on_unanswerable: relay`, `driver: hold-stream | park-resume`, `max_approval_wait`); allow
   `relay` in the enum *only when* a driver is declared. Resolution + validation; no behavior yet.
2. **The relay orchestrator (core, driver-agnostic).** In the harness node, when an
   `approval_requested` arrives under `relay`: policy consult → auto-approve, else submit a
   `ReviewItem` to the `ReviewQueue`, wait up to `max_approval_wait`, decide, audit. Feed-back via an
   injected fake driver (tested without a real harness). Timeout → abort. Scoped approval.
3. **`park-resume` driver.** The most generic real driver: cancel + checkpoint the resume token,
   surface to the inbox, and on approval re-launch through the declarative engine with the resume arg
   + an expanded-grant arg (a new adapter `grant.arg` template). Reuses the existing checkpointer +
   `swarmkit`-resume path. e2e-testable against real `claude` for a short wait.
4. **`hold-stream` driver (Claude Code reference).** The live bidirectional path: launch with
   `--input-format stream-json`, translate the control-protocol permission request ↔ decision. The
   richer seat; short-wait, no re-launch. Gated e2e against real `claude`.
5. **CLI + cockpit surface.** `swarmkit review` already lists/approves; extend it to render a harness
   approval request (capability, rationale, node) and record the response. Audit projection shows the
   gate; the trace gets an approval-gate child span (human-wait duration flagged, per RFC §8.1).

## Explicitly deferred (after relay)

§6.3 **input requests** ("what do you want?") + lead-node escalation + the shared question classifier
(a distinct, larger piece); **P3.5** trust-accrual → allowlist changeset proposals (relay is the
evidence-gathering phase this builds on); cross-boundary W3C traceparent. relay ships the permission
half of the interactive tier; judgment questions are next.

## Acceptance (subset of RFC §10)

- Under `on_unanswerable: relay`, an out-of-grant request interrupts the node, appears in the approval
  inbox, and after a response the run resumes and completes; the approval is scoped to the one action
  (RFC #9).
- A policy-allowed capability is auto-approved with no human interruption.
- A relay request with no response inside `max_approval_wait` degrades to `abort` and never hangs
  (regression vs the headless-hang mode, RFC #8).
- The audit log records every approval request/response with responder identity; disabling traces
  does not disable it (RFC #16).

## Test / demo plan

- **Unit:** the orchestrator with a fake driver + fake governance — auto-approve path, inbox-approve
  path, deny path, timeout→abort, scope assertions, audit records.
- **Integration:** a mock harness emitting `approval_requested` mid-stream → asserts the full flow
  (inbox item, decision fed back, single scoped approval, resume).
- **e2e (gated):** real `claude` under `park-resume` — a task needing a tool outside the grant pauses,
  is approved via the inbox, resumes with `--allowedTools`, and completes.
