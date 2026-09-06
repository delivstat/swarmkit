---
title: Taking communication out of the runtime
description: Why channel integration belongs to the calling application, what leaves, and the event seam that replaces it.
status: draft
---

# Taking communication out of the runtime

**Status:** proposed. The precedent is
[`extracting-the-pipeline.md`](extracting-the-pipeline.md) — the same decision, one layer over.

## The claim

**SwarmKit should not talk to Telegram.** An application that uses SwarmKit as its agent runtime
already owns its relationship with its users: which channel, which identity, what tone, what
retention. The runtime's job is to say *what happened*, in a form the application can act on.

This is the pipeline decision restated. Sequencing left in 1.189.0 because it belonged to the
caller; communication belongs to the caller for the same reason and slightly more obviously, since
a chat integration is a relationship with a third party rather than a piece of agent machinery.

## Why the evidence was already in

Three things happened while building this, and each argued against it:

**The notification providers were never reachable.** 548 lines covering Telegram, Discord, Slack
and a generic webhook, with 31 unit tests and a docstring promising it fired on `hitl_requested`,
`run_ended_error` and `skill_gap_surfaced`. `build_provider` was never called, `NotificationRegistry`
never constructed, and `notifications:` did not exist in the schema. Code that nobody could reach
for months is code nobody needed *in the runtime*.

**The inbound half does not fit the runtime's shape.** Telegram works because `getUpdates` is
outbound long-polling. Discord needs a gateway WebSocket held open; Slack needs Socket Mode. Both
mean a persistent connection with its own lifecycle inside a process that is otherwise
request-shaped, and both were deferred for exactly that reason.

**Third-party surfaces rot on their own schedule.** WhatsApp needs QR pairing against a personal
number. Slack moved to Socket Mode. A runtime that ships a channel adapter releases when Telegram
deprecates something, which is a release cadence with nothing to do with agents.

## The argument that settles it

A notification provider is a **parallel extension mechanism**. Invariant #2 says skills are the only
capability primitive; a provider is a second way for the runtime to reach the outside world, one
that no skill grant authorises and no audit line records. That is the smell, and it is why the code
could sit unreachable without anything noticing: nothing depended on it because nothing *could*.

An event sink does not have that problem. Events are already produced, already append-only, already
observable. Publishing them adds no new authority.

## What leaves

| component | LOC | verdict |
| --- | --- | --- |
| `notifications/` (5 providers, registry, store) | 548 | **leaves** |
| `channels/` (`_config`, `_server`, MCP tools) | 416 | **leaves** |
| `reference/skills/channel-{send,ask,replies}.yaml` | 3 files | **leaves** |
| `tests/test_notifications.py`, `tests/test_channels.py` | 633 | **leaves** |
| `examples/channels/`, `just demo-channels` | — | **leaves** |
| `channels:` in `workspace.schema.json` + 2 fixtures | — | **leaves** |
| `mcp_servers` entry for `channels` in `reference/workspace.yaml` | — | **leaves** |

Roughly 1,600 lines. **None of it is published** — channels shipped in 1.206.0 and never reached
PyPI, and the notification providers were never reachable at all. So this is a deletion rather than
a deprecation, which is the difference between a clean removal and a two-release dance. That window
closes at the next publish.

## What replaces it

An **event sink**: the runtime pushes what happened, the application decides what to do about it.

```yaml
# workspace.yaml
events:
  - sink: webhook
    url: https://my-app.internal/swarmkit/events
    credentials_ref: my-app-signing-key
  - sink: stdout          # dev, and the honest default
```

The vocabulary already exists and already flows into the audit provider — `run.started`,
`run.ended`, `hitl.requested`, `hitl.resolved` — with `run.paused` and `run.resumed` to add.

**But there is no `/events` route.** The events reach the audit store and nothing exposes them over
HTTP; `GET /events` returns 404 today. So the durable-pull half of the delivery contract below is
*build*, not wiring, and it is the reason this sequences before the deletion rather than with it.

## Delivery: best-effort push, durable pull

The interesting decision, and the one to get right rather than fast.

A webhook that fails is not a rare case. If `hitl.requested` is dropped, a run waits forever for an
approval nobody was told about — the failure mode is silence, which is the worst kind.

Two honest options:

1. **At-least-once with retry and a queue.** Correct, and it puts durable delivery machinery inside
   the runtime — a queue, a retry schedule, a dead-letter path, backpressure. That is the thing we
   just decided not to own.
2. **Best-effort push plus a durable pull.** The webhook is fire-and-forget with a short retry; the
   audit log is already durable and ordered, so the application reconciles from a cursor
   (`GET /events?after=<seq>`). A missed push costs a reconciliation, not an approval.

**Decision: option 2.** The push is a latency optimisation over a source of truth that already
exists, and saying so plainly is better than implying a guarantee the runtime is not built to keep.
An application that needs stronger delivery puts a queue between itself and the webhook, which is
where a queue belongs.

The contract stated for callers:

- Events are **at-most-once** over the webhook. Retried a small fixed number of times, then dropped.
- Every event carries a monotonic `seq` and the run id.
- `GET /events?after=<seq>` replays from the durable log. **An application that cares about not
  missing events reconciles; one that does not, does not.**
- The portal remains the reliable human path. A gate is visible there whether or not any push
  succeeded.

## Asking a human needs nothing new — it is a gate

`channel_ask` is not replaced by an MCP tool either. It is **deleted**, because the runtime already
has this primitive and has had it since the decision-skills work:

```python
ReviewItem.answer      # "For §6.3 input requests: the operator's textual answer"
ReviewItem.resolved_by # "The authenticated resolver"
ReviewItem.comment     # "What the human said. Relayed to the agent, recorded on the audit."
```

`channel_ask` reimplemented that with **worse durability** — blocking in-process on a long poll
rather than checkpointing — and **worse identity**, a `chat_id` rather than an authenticated
`resolved_by`. An earlier draft of this note proposed replacing it with an `ask_human` MCP tool,
which is the same mistake once more: a synchronous tool call holds a session open while a human is
at lunch, which `mcp-oauth.md` already rejected for the analogous consent case.

**The runtime's role is to surface the ask, park, and accept a resolution. How it is resolved is
never its business.**

### Verified, not assumed

Driven end to end over HTTP against `swarmkit serve`, which is what an application would do:

```
POST /run/ask                    job 621f8a…, status: running
                                 status: deferred        ← parks; nothing resident
GET  /review                     mpa-621f8a…:root-0-operator
                                 reason: "role 'operator' must approve 'workspace:approve'"
                                 question · options · free_text_allowed · gate_id · run_id
GET  /gates/621f8a…:root         outstanding: ["operator (workspace:approve)"]
POST /review/{item}/resolve      status: approved, resolved_by: anonymous
GET  /gates/…                    resolved: true, distinct_approvers: ["anonymous"]
POST /jobs/{id}/resume           completed
```

Four things that run-through found, each of which this note would have got wrong from reading the
code alone:

**A gate silently degrades to advisory when no `RoleRegistry` defines its roles.** The first run
completed with no gate at all. The guard is deliberate — a gate nobody can satisfy would strand
every run — but an application seeing no gate cannot tell "not gated" from "misconfigured". The
event vocabulary needs to distinguish them.

**Two endpoints look like approve and one silently does not count.** `/review/{id}/approve` marks
the item approved and leaves `resolved_by` empty, so the gate stays `pending` with no
`distinct_approvers` and the run re-defers. `/review/{id}/resolve` is the multi-party one that
resolves as the authenticated caller. Any application integrator will hit this; the first attempt
here did.

**`outcome` is `"approve"` while the resulting status is `"approved"`.** Small, and it costs a round
trip to discover.

**A resolved gate does not resume its run.** An explicit `POST /jobs/{id}/resume` is required.

### Decision: a resolved gate resumes its run

Automatic, with an opt-out. Otherwise every application writes the same resume call, and the one
that forgets leaves a run parked after its gate has been satisfied — a stall with no visible cause,
because everything *looks* resolved.

The opt-out exists for an application that wants to batch or delay resumption, and it is a
workspace-level setting rather than a per-call flag, so the behaviour is legible in one place.

## The reference application

The pipeline removal worked because `examples/pipeline-orchestrator/` proved the pattern and
**imports no runtime module**. Without an equivalent, "the application should do it" is an
instruction rather than a demonstration.

So: `examples/event-consumer/` — a small service that receives the webhook, reconciles from the
cursor on startup, and sends a Telegram message when a gate opens. It imports `httpx` and nothing
from `swarmkit_runtime`. The Telegram code that leaves the runtime lands there, where it is a
hundred lines of somebody's application rather than a supported surface.

## Order: events first, then the deletion

Deleting channels before `/events` exists would leave a window where nothing can observe a gate
except polling `/review`. So:

1. **`GET /events?after=<seq>` and the `events:` sink**, with `run.paused` / `run.resumed` added to
   the vocabulary and gate events carrying enough to render a question.
2. **Auto-resume on gate resolution**, with the opt-out.
3. **`examples/event-consumer/`**, proving the loop against a running serve.
4. **The deletion** — all 1,600 lines, once nothing needs them.

## What this does not change

- **`swarmkit ask`** is unrelated — it asks an LLM about a workspace, not a human.
- **Approval gates and the review queue** are untouched. The event says a gate opened; resolving it
  goes through the API that already exists.
- **OAuth's `expiring_soon`** becomes an event like everything else, which is what it wanted to be.

## Test plan

- Every deleted module's tests are deleted, not adapted. A test that survives a removal is testing
  something that did not leave.
- The sink: an event reaches a webhook; a failing webhook does not fail the run; the retry count is
  bounded; `seq` is monotonic across a restart.
- Reconciliation: an application that misses N pushes recovers all N from the cursor.
- The negative, as with the pipeline: `grep -rn "telegram\|discord\|slack" packages/runtime/src`
  returns nothing.

## Demo plan

`just demo-event-consumer` — start `swarmkit serve`, run a topology that opens a gate, and watch
the example application receive `hitl.requested` and print the message it would send. Then kill the
consumer, run again, restart it, and watch it recover the missed event from the cursor. **The
recovery is the demo**: anyone can show a webhook firing.
