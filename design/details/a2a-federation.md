---
title: A2A federation — SwarmKit agents that hand back their record
description: When both sides of an A2A call are SwarmKit, the callee returns its run id, token/cost usage and an observability pointer, the caller stitches them into its own trace and audit, and the card says so at add time. A budget passed on the call is honored by a SwarmKit callee.
tags: [a2a, interop, observability, audit, budget]
status: accepted
---

# A2A federation — SwarmKit agents that hand back their record

## The gap

An `agent` skill can call a remote agent through its A2A card (`a2a-interop.md`). The call returns
an answer — and nothing else. The caller's trace stops at "called `agent__x`, got text"; its audit
has no link to what the remote run did; its `GET /usage` does not count the tokens the remote
spent; and a budget on the calling node does not reach the remote at all. Across the network the
call graph, the cost and the record all go dark at the boundary (`nested-call-limits.md` frames the
same problem for depth and budget).

We cannot fix that for an arbitrary remote — a non-SwarmKit agent, or one we do not run, owes us
nothing. **But when the callee is also SwarmKit, it already has all of it**: a run id, a durable
audit journal (`audit-event-journal.md`), per-run token/cost totals on the job row, and a
`GET /events`/`GET /audit` surface. The gap is that none of it crosses back. This closes that,
between SwarmKit instances, over the A2A metadata channel — no protocol extension a non-SwarmKit
agent has to understand, just extra keys it ignores.

## Goal

1. **Identify.** A SwarmKit instance's Agent Card declares a federation extension, so adding the
   agent (portal probe, `GET /api/a2a/probe`) can show "this is a SwarmKit agent" and what it will
   hand back.
2. **Return.** A SwarmKit callee puts its `run_id`, token/cost `usage`, and an observability
   pointer into the task's `metadata.swarmkit`. The caller reads them, records an
   `a2a.remote_usage` audit event linking the two runs, and can pull the remote's events/audit by
   `run_id` from the same endpoint with the same credential.
3. **Budget** (slice 2). The caller passes its remaining allowance in the message metadata; a
   SwarmKit callee installs it as the child run's budget. Advisory across the trust boundary — a
   SwarmKit callee honors it, anyone else ignores it, and a returned total is the callee's claim,
   not an audited fact on our side.

## Non-goals

- Not a guarantee about non-SwarmKit agents, and not trust in a remote's numbers beyond "recorded
  as reported". The `a2a.remote_usage` event is attributed to the remote and labelled as its claim.
- Not returning the remote's audit *content* across the boundary. We return a pointer (`run_id` +
  the fact that `GET /events`/`GET /audit` answer there); pulling it still takes the caller's own
  authorization against that endpoint. A run's events can quote a document that can quote a secret;
  they do not get shipped in a task result.
- Not a new transport. Everything rides A2A `metadata`, which the spec leaves open.

## Design

### The card says it (identify)

`build_agent_card` adds an A2A extension under `capabilities.extensions`:

```json
{ "uri": "urn:swarmkit:a2a:federation:v1",
  "description": "Returns run id, token/cost usage and an observability pointer per task; honors a passed budget.",
  "params": { "runtime": "1.240.0", "returns_usage": true, "returns_observability": true, "honors_budget": true } }
```

A non-SwarmKit client ignores `extensions`. `fetch_card` parses it into `AgentCard.swarmkit`
(None when absent). `GET /api/a2a/probe` returns `is_swarmkit` + `runtime` + the flags, and the
portal's "Add remote agent" shows a **SwarmKit** badge. The written skill needs no schema change:
the card is fetched and cached on first use, so the runtime knows at call time whether to expect
the rich round-trip.

### The callee returns it

`task_from_job` adds to `metadata.swarmkit`, on a terminal task:

```json
"run_id": "<job id>",
"usage": { "input_tokens": 1234, "output_tokens": 567, "cost_usd": 0.0189 },
"observability": { "events": true, "audit": true }
```

`run_id` is the callee's job id (also the A2A task id, but named explicitly). `usage` is the job
row's `usage_*` totals, omitted when zero/absent. `observability` says the caller can
`GET /events?run_id=…` and `GET /audit` at this endpoint — a hint, not the data.

### The caller stitches it

In `_call_remote`, once the task is terminal, read `task.raw["metadata"]["swarmkit"]` and emit:

```
a2a.remote_usage  { card, skill_id, remote_run_id, input_tokens, output_tokens, cost_usd,
                    endpoint, source: "reported" }
```

So the caller's own audit — durable, queryable by its run id — carries the link to the remote run
and its cost, attributed as the remote's report. `swarmkit logs`/`GET /audit` on the caller then
shows the cross-boundary hop, and a later `GET /usage` roll-up can include reported remote spend
kept distinct from locally-metered spend.

### Budget forward (slice 2)

The caller adds `message.metadata.swarmkit.budget = { max_cost_usd, max_turns,
max_wall_clock_minutes }` — its remaining allowance, derived from the calling node's envelope minus
what it has spent. `send_message` on a SwarmKit callee reads it and installs it as the child run's
budget/circuit-breaker (the same seam `_begin_run` uses). This is the cross-instance half of
`nested-call-limits.md`: the local depth/budget context cannot cross the network, but a value on
the A2A envelope can. Enforcement is the callee's; the caller records what it asked for.

## Test plan

- Two SwarmKit instances over an in-process ASGI transport (as `test_a2a_*` do): a caller
  `agent` skill calls a callee topology; assert the returned task carries `metadata.swarmkit.run_id`
  and `usage`, and the caller's audit has an `a2a.remote_usage` event naming the callee's run id.
- `fetch_card`/probe: a SwarmKit card yields `is_swarmkit=True` + flags; a plain card yields False.
- Slice 2: a budget in the message metadata reaches the callee run's tracker; a non-SwarmKit
  message (no budget) runs unbounded as before.

## Demo plan

Extend the Level 20 (agents calling agents) tutorial: call one serve instance from another and show
the caller's `swarmkit logs` carrying the remote run id and cost. `just demo-...` reuses the A2A
both-ways demo workspace.
