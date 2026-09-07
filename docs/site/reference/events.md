# Events

SwarmKit tells an application what happened. The application decides who hears about it, on which
channel, and in what words.

That division is deliberate. The runtime used to ship Slack, Discord and Telegram providers; they
were removed in 1.216.0 because a chat integration is a relationship with a third party, not a
piece of agent machinery — the same argument that took stage sequencing out in 1.189.0. See
[extracting the channels](https://github.com/delivstat/swarmkit/blob/main/design/details/extracting-the-channels.md).

## Two halves, deliberately unequal

```
                    ┌──────────────────────────────────────────┐
  run.started       │  GET /events?after=<cursor>   DURABLE     │
  run.ended         │      the log an application reconciles    │
  funnel.gate_opened│      from; nothing here is ever lost      │
  hitl.requested    ├──────────────────────────────────────────┤
  hitl.resolved     │  events: sinks                BEST EFFORT │
  …                 │      a webhook, so an application does    │
                    │      not have to poll                     │
                    └──────────────────────────────────────────┘
```

**The pull is the source of truth.** The push is a latency optimisation over it.

## Reading events

```
GET /events?after=<cursor>&types=funnel.gate_opened,run.ended&run_id=&limit=100
```

Returns events in **log order, oldest first** — a consumer replays forward from where it stopped.
(`GET /audit` is the other direction, newest first, for a human reading recent history.)

```json
{
  "events": [
    {
      "cursor": "MjAyNi0wOS0wN1QxMjowMDowMCsw…",
      "event_type": "funnel.gate_opened",
      "run_id": "b7f1eb46c983",
      "topology_id": "ask",
      "payload": {"gate_id": "b7f1eb46c983:root"}
    }
  ],
  "next_cursor": "MjAyNi0wOS0wN1QxMjowMDowMCsw…",
  "has_more": false
}
```

The cursor is **opaque** — pass back what you were given. Every event carries its own, so an
application that crashes mid-page resumes without skipping what it never processed. An empty page
echoes your cursor back, so an idle consumer need not remember where it was. A cursor this API did
not issue is a `400`, never a replay from the beginning.

## Pushing events

```yaml
# workspace.yaml
events:
  - sink: webhook
    url: https://my-app.internal/swarmkit/events
    credentials_ref: app-signing-key   # sent as a bearer token
    types: [funnel.gate_opened, hitl.requested, run.ended]
  - sink: stdout                        # development
```

**The delivery contract, stated rather than implied:**

| | |
| --- | --- |
| guarantee | **at-most-once** |
| retry | a small fixed number of attempts, then dropped |
| `4xx` | not retried — the application rejected the body |
| a failing sink | logged; it never fails a run |
| a dropped event | still readable at `GET /events?after=` |

There is no queue, no dead-letter, no backpressure. An application needing stronger delivery puts a
queue between itself and the webhook, which is where a queue belongs.

## Asking a human

A swarm that needs a person opens a **gate**; it does not send a message. The runtime surfaces the
ask and parks the run — how it is resolved is the application's business:

```
funnel.gate_opened ──►  GET /gates/{gate_id}          what is it waiting on?
                        (ask your user, however you like)
                   ◄──  POST /review/{item_id}/resolve
                        {"outcome": "approve", "comment": "…"}
                        → the run resumes automatically
```

`gates.auto_resume: false` turns that resumption off for an application that batches;
`POST /jobs/{id}/resume` works either way.

Three things worth knowing before writing a consumer:

- **Acknowledge before working.** The event is already durable when it arrives. Answering non-2xx
  makes the runtime retry and then drop.
- **Be idempotent per gate.** A gate can be announced more than once — a resuming run re-enters the
  gated node before finding the existing decision. `GET /gates/{id}` reports `resolved`.
- **`funnel.advisory_completed` is not "no gate".** It means a funnel declared an approve layer that
  could not be enforced — usually a role no `RoleRegistry` defines — so the run continued
  **unreviewed**. Treating it as ungated silently loses an approval step.

## A worked example

[`examples/event-consumer/`](https://github.com/delivstat/swarmkit/tree/main/examples/event-consumer)
is a complete application: it reconciles from a cursor on startup, receives pushes, asks a human on
Telegram, resolves the gate, and imports no `swarmkit_runtime` module. Its only third-party
dependency is `httpx`.

```bash
just demo-event-consumer
```
