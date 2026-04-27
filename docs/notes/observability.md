---
title: Observability discipline
description: Every runtime path emits structured audit events. Every new skill declares its audit block. If a path isn't observable, it isn't done.
tags: [discipline, observability, runtime, review]
status: active
---

# Observability discipline

Swael's user-facing promise for a running swarm is that every decision is inspectable — via `swael logs`, `swael events`, `swael review`, `swael why`, and `swael ask`. That promise only holds if every runtime path emits structured audit events. This note is the per-PR reminder.

## The rule

**A runtime path is not done until it emits structured audit events** with the field set from `design/details/human-interaction-model.md` Layer 1. A feature PR that introduces a new skill invocation site, a new HITL gate, or a new policy-decision point without matching audit events is incomplete.

## Per-PR checklist

For any PR touching the runtime (skill dispatch, agent execution, governance wiring, HITL flow, MCP calls):

- [ ] **Every new call site emits an `AuditEvent`** via `GovernanceProvider.record_event`. No bespoke logging. No `print`. No swallowed events.
- [ ] **Event fields are complete.** `run_id`, `agent_id`, `skill_id` or workspace-event-kind, `timestamp`, `duration_ms`, `policy_decision`, cost/token info if a model was called, `verdict` + `reasoning` for decision skills.
- [ ] **Redaction is honoured.** If the skill declares `audit.log_inputs: summary` or `redact: [...]`, the emitted event reflects that — not the raw data.
- [ ] **Error paths emit too.** A skill that fails still emits one event with `error: { type, message, ... }`. "Silent failure" is never acceptable.
- [ ] **Tests assert the events.** `MockGovernanceProvider.captured_events` is the assertion target — check that the PR's new code emitted what the user's `swael logs` run would show.

For any PR touching a skill schema or adding a new skill under `reference/` or `examples/`:

- [ ] **The skill declares its `audit:` block** if the category defaults don't apply. Decision skills inherit `log_outputs: full` on `reasoning`; capability skills inherit `summary`. Override explicitly when the defaults don't fit.
- [ ] **Redaction paths are listed** if the skill handles PII / secrets / customer data. Better to over-redact and relax later than to leak.

## Anti-patterns

- **`print()` in runtime code.** Events go through `GovernanceProvider.record_event`; the CLI formatters decide how to display them. Never direct-print.
- **Logging exceptions without an audit event.** A caught exception without a corresponding `error:`-populated event is a hole in the log.
- **Custom log shapes per skill.** The event schema is pinned; don't invent fields. If a skill has genuinely unique context, put it in `outputs` (redactable) or propose a schema addition.
- **Skill-level `log_inputs: full` in prod.** Governance layer enforces `workspace.audit.level` clamping; if you think you need `full`, you probably need `summary` + a targeted `redact` list.

## Why this matters

Three concrete user paths depend on it:

1. **`swael logs <run-id>`** — scripted pipelines grep the JSON. If fields are inconsistent the scripts break.
2. **`swael ask "..."`** — the observer sends events as LLM context. Missing events → wrong answers. Raw PII in events → privacy breach.
3. **Post-hoc audit / compliance** — "what did the swarm do on run X" must be answerable six months later. If events were skipped, the answer doesn't exist.

## See also

- `design/details/human-interaction-model.md` — the authoritative event schema and CLI surface
- `design/Swael-Design-v0.6.md` §16.4 (audit logging), §8.3 (media pillar, append-only)
- `docs/notes/usability-first.md` — the broader per-PR checklist this note extends
- Tasks #33–#37 — implementation PRs for audit schema, CLI primitives, `swael ask`, notifications
