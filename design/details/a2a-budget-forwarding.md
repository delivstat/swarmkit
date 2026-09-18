---
title: A2A budget forwarding — the caller passes an allowance the SwarmKit callee honors
description: Federation slice 2. The caller sends its remaining budget in the A2A message metadata; a SwarmKit callee installs it as the child run's budget, closing the cross-instance half of nested-call budget.
tags: [a2a, interop, budget, governance]
status: proposed
---

# A2A budget forwarding

Slice 2 of A2A federation (`a2a-federation.md`; slice 1 shipped the return of run id / usage /
observability in 1.240.0). This is the enforcement half.

## The gap

`nested-call-limits.md`: a budget on a calling node does not reach a nested agent, and across the
network it cannot — the local depth/budget `ContextVar` does not cross an HTTP hop. So a coordinator
with `max_cost_usd: $5` bounds only its own node; an A2A callee runs on its own limits, and the
tree's total is discoverable (walk `parent_job_id` / `a2a.remote_usage`) but not enforceable as one
number.

## Goal

The caller passes its **remaining** allowance on the A2A call; a SwarmKit callee installs it as the
child run's budget. Advisory across the trust boundary — a SwarmKit callee honors it, a
non-SwarmKit one ignores it, a hostile one could lie — so the caller records what it asked for and
still trusts nothing it did not meter. This is the cross-instance twin of the in-process nested
budget.

## Non-goals

- **Not enforcement on a remote we do not run.** Enforcement is the callee's; we record the ask.
- **Not a new budget model.** Reuses `BudgetEnvelope` (`max_cost_usd`, `max_turns`,
  `max_wall_clock_minutes`) — the same one harness nodes carry.
- **Not automatic for model-only callees.** A budget bounds a run; how a callee applies it (harness
  node envelope, a run-level circuit-breaker limit) is the callee's business. The card flag says
  whether it honors one at all.

## Design

### Caller sends it

In `agent_skill/_executor._call_remote`, when the card's federation extension declares
`honors_budget: true`, attach to the outgoing `message.metadata.swarmkit.budget`:

```json
{ "max_cost_usd": 3.20, "max_turns": 12, "max_wall_clock_minutes": 8 }
```

The value is the calling node's envelope **minus what it has already spent** (from the run meter),
so nesting narrows the allowance the way the in-process depth counter narrows depth. Absent a
declared budget on the caller, nothing is sent (unbounded, as today). The ask is audited:
`a2a.budget_forwarded { card, endpoint, max_cost_usd, max_turns, ... }`.

### Callee honors it

`server/_a2a.A2AHandler.send_message` reads `message.metadata.swarmkit.budget` and threads it into
the run it starts, installed as the child run's budget/circuit-breaker at the same seam
`_begin_run` uses (`limits_from_workspace` merged with the passed ceiling, whichever is tighter —
a passed budget never *raises* the workspace's own limit). When the run breaches it, it ends
`budget_exceeded`, which maps to the A2A task state the caller already handles, and the returned
`metadata.swarmkit.usage` shows the spend at the stop.

### Card advertises it

`build_agent_card` flips the federation extension's `honors_budget` to `true` once the callee
applies passed budgets, so a caller only forwards to an instance that will act on it.

## Test plan

- Two SwarmKit instances (ASGI transport): the caller declares a small `max_cost_usd`; assert the
  callee's run received the budget (its tracker carries the passed ceiling) and, when a scripted
  run exceeds it, the task ends `budget_exceeded` and the returned usage reflects the stop.
- The passed budget never raises the callee's own workspace limit (min of the two).
- A caller with no declared budget sends no `budget` key; a callee with `honors_budget: false`
  ignores a sent one (older callee) — both run as today.
- `a2a.budget_forwarded` audit event on the caller.

## Demo plan

Level 20 (agents calling agents): a coordinator with a tight budget calls a remote that would
otherwise run long; show the remote stopping at the forwarded ceiling and the caller's audit
carrying both the ask (`a2a.budget_forwarded`) and the reported spend (`a2a.remote_usage`).
