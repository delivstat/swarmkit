# An event survives a failed handler and a killed worker

Status: implemented (runtime 1.145.0). Closes bug 12 in `docs/notes/reported-bugs.md`.

## The failure

`run_drive_loop()` had no error handling around `handle_event`:

```python
event_id, correlation_id, event = claimed
await controller.handle_event(correlation_id, event)   # any exception escapes
store.ack(event_id)                                    # never reached
```

Any exception propagated out of the loop, out of `asyncio.run()`, and the process exited — after the
event was claimed and before it was acked. It was then **unrecoverable**: `claim()` only ever
selected `queued` rows, and there was no `claimed_at`, no visibility timeout and no reclaim path.

A restarted orchestrator polled forever past an event it could never pick up, while the saga sat
`active` with `updated_at` frozen at the crash. Nothing reported an error — `pipeline status` showed
a normal in-progress run, which is why it read as a slow stage for over an hour.

The docstring asserted the opposite of the behaviour: *"a crash re-drives from the store."* A crash
is precisely the case that could not re-drive.

Triggered by WSL's `autoProxy` pointing `HTTP_PROXY` at a dead port, so the orchestrator's
**loopback** call to serve raised `ConnectError`. Recovery took direct SQL against
`pipeline_events`, because re-emitting is refused for an existing active saga and there was no gate
to clear.

## Design

Three changes. The first alone removes the permanent-stall class; the second covers what no
`except` block can; the third removes this specific trigger.

### 1. An event's fate is decided explicitly

The handler is wrapped, and a failure either goes back to the queue or is dead-lettered:

```python
try:
    await controller.handle_event(correlation_id, event)
except Exception as exc:
    store.release(event_id, str(exc)) if attempts < max else store.fail(event_id, str(exc))
    continue
store.ack(event_id)
```

Unbounded retry is not acceptable — a deterministically-failing event would spin forever, and this
loop drives real work at real cost. So the bound is persisted (`attempts`), and an event that
exhausts it becomes terminally `failed` rather than being retried into eternity or silently dropped.

### 2. A claim expires

`claim()` also takes rows whose claim is older than a visibility timeout, so an event survives what
an `except` block never sees: SIGKILL, an evicted container, a lost machine.

**Both paths increment the same counter**, because `claim()` is where it is incremented. That is
what bounds a crash loop as well as a failure loop — a worker that dies mid-event repeatedly will
dead-letter the event rather than crash-looping on it forever.

The timeout defaults to 5 minutes. A stage run can legitimately take much longer than that, so the
claim is **refreshed while the handler runs** (a heartbeat), rather than the timeout being set to
some guess about the longest plausible stage. A guessed ceiling is how you get an event stolen from
a worker that was doing fine.

### 3. Loopback does not go through a proxy

`httpx` honours `HTTP_PROXY` even for `127.0.0.1`. Routing the orchestrator's control-plane call to
serve through an ambient proxy is never what an operator wants, so that client sets
`trust_env=False`. This alone would have made the reported failure impossible.

### Making it visible

The silence is what made this take hours. A dead-lettered event is surfaced by `swarmkit pipeline
status`, and every release/fail is logged with the reason and the attempt count.

## Schema

Three additive columns on `pipeline_events`: `claimed_at`, `attempts`, `last_error`.

`create_all` does not ALTER an existing table, and the runtime had no migration hook — the reason
the rework-comment fix (1.137.0) put its data in an existing JSON column instead. Here that is not
available: `claim()` has to filter and order on these values in SQL. So this adds the additive
migration the control-plane's stores already have (`_registry._migrate`), guarded by column
inspection and applied to both dialects, since Postgres is a first-class backend here.

An existing deployment therefore upgrades in place rather than failing on the next insert.

## Worker identity

The default worker name was `orchestrator-1` for **every** process, so two orchestrators on one
store were indistinguishable in `claimed_by` and raced for the same events. The default is now
host+pid derived. This matters more once claims expire: "whose claim is this" becomes a question the
data has to be able to answer.

## Test plan

`packages/runtime/tests/test_orchestrator_event_recovery.py`:

- a handler that raises does not kill the loop, and the event returns to `queued`
- the reported scenario end to end: a `ConnectError` on the first attempt, success on the retry
- retries are bounded; an event that keeps failing is dead-lettered as `failed`, not retried forever
- a dead-lettered event records why, and is visible rather than silently dropped
- a claim older than the visibility timeout is reclaimable; a fresh one is not
- the heartbeat keeps a long-running handler's claim alive
- a crash loop is bounded by the same counter as a failure loop
- `ack` still ends the event; a successful handler is unchanged
- the migration adds the columns to a pre-existing table and is idempotent
- the loopback client does not trust ambient proxy env vars
- two workers get distinct default names

## Demo

`just demo-orchestrator-recovery` — an event whose handler fails transiently, then succeeds; and one
that fails deterministically until it is dead-lettered.
