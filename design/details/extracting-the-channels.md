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
`run.ended`, `hitl.requested`, `hitl.resolved` — with `run.paused` and `run.resumed` to add. This is
a sink, not a new subsystem.

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

## Asking a human is a skill, not an event

`channel_ask` is the one piece that is genuinely not an event: an agent deciding mid-run that it
needs an answer, and blocking on one, is a *capability the agent invokes*.

It survives without any runtime transport. The application exposes an `ask_human` MCP tool; the
agent holds a skill bound to it; SwarmKit governs the call like any other. The runtime keeps its
half — the skill grant, the effects declaration, the gate — and owns no chat client. That is
strictly better than what exists now, because the application already knows who to ask and on which
channel.

This is also the answer to *"how does my app get a reply back?"* — through the same MCP tool call,
synchronously, which the shipped `channel_ask` had to invent long-polling to achieve.

## The reference application

The pipeline removal worked because `examples/pipeline-orchestrator/` proved the pattern and
**imports no runtime module**. Without an equivalent, "the application should do it" is an
instruction rather than a demonstration.

So: `examples/event-consumer/` — a small service that receives the webhook, reconciles from the
cursor on startup, and sends a Telegram message when a gate opens. It imports `httpx` and nothing
from `swarmkit_runtime`. The Telegram code that leaves the runtime lands there, where it is a
hundred lines of somebody's application rather than a supported surface.

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
