# Event consumer — an application that decides who to tell

The reference application for [`extracting-the-channels.md`](../../design/details/extracting-the-channels.md).
SwarmKit says *what happened*; this decides *who hears about it, where, and in what words*.

**It imports no `swarmkit_runtime` module** — the same rule `examples/pipeline-orchestrator/`
follows, and the reason that removal stuck rather than being an instruction. Its only third-party
dependency is `httpx`; the webhook endpoint is `http.server`, to show how little is actually needed.

## The loop

```
SwarmKit                          this application
────────                          ────────────────
run parks on a gate
  └─ POST /swarmkit/events ─────► funnel.gate_opened
                                    GET /gates/{id}      what is it waiting on?
                                    ask a human          Telegram, Slack, a ticket…
                                  ◄── POST /review/{item}/resolve
gate satisfied → run resumes
  └─ POST /swarmkit/events ─────► run.ended
```

The Telegram code here is deliberately the **application's**. It used to live in the runtime, tied
to somebody else's API deprecations; a hundred lines in an example is where a chat integration
belongs. Swap `Telegram` for Slack, email or a ticket and nothing else moves.

## Run it

```bash
swarmkit serve ./my-workspace --port 8000 &
python examples/event-consumer/consumer.py --serve http://127.0.0.1:8000 --port 9000
```

With `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set it sends; without them it prints what it would
send, so the demo runs for anyone.

Point the workspace at it:

```yaml
events:
  - sink: webhook
    url: http://127.0.0.1:9000/swarmkit/events
    types: [funnel.gate_opened, funnel.advisory_completed, run.ended]
```

## Four things this demonstrates, each learned by getting it wrong

**Reconcile on startup.** Delivery is best-effort: if this process is down, the push is lost. The
first thing it does is replay from `GET /events?after=<cursor>`, which is what makes a lost push
survivable rather than an outage. Verified by killing it, running a topology, and restarting.

**Acknowledge before working.** An earlier version raised out of the handler when a gate lookup
404'd — so it answered non-2xx, the runtime retried three times and dropped, and a notification was
lost while the event sat safely in the log the whole time. The event is already durable when it
arrives; a failure on this side is this side's problem.

**Be idempotent per gate.** A gate can be announced more than once: a resuming run re-enters the
gated node and re-emits before finding the existing decision. Without the `resolved` check this
asks the same human the same question twice.

**`funnel.advisory_completed` is not "no gate".** It means a funnel declared an approve layer that
could not be enforced — usually a role no `RoleRegistry` defines — so the run continued
**unreviewed**. An application that treats it as ungated silently loses its approval step, which is
why this one warns loudly.

## What it deliberately does not do

No retry of its own, no queue, no state beyond a cursor file. The cursor is the only durable thing
an event consumer needs, because the log it points into is already durable.
