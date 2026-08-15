# Stopping a run

**Status:** implemented — runtime 1.193.0.

One thing changed between design and implementation, and the **demo** is what caught it. The first
version cached a "not stopped" answer for a second, reasoning that a thirty-agent run should not
issue thirty round-trips. Run against a fast pipeline, the stop was not late — it was *missed*
entirely, and the run completed. One indexed primary-key SELECT against a node that takes seconds to
minutes is not a cost worth a feature that sometimes does nothing, so the check now asks every time.
A *seen* stop still latches: the run is already raising.

`swarmkit stop <run-id>` has been a `_not_implemented("stop", milestone="M6")` stub since M6, with a
docstring that already promises the semantics: *"Requests the runtime to checkpoint state and abort
the current run. The run can be resumed later with `swarmkit run --resume`."* This note makes that
true, and says plainly what it cannot do.

## Goal

Let a human stop a run they no longer want — from another terminal, from the UI, from a script —
**without losing the work it has already done and paid for**, and let them resume it later if they
change their mind.

## The problem it solves

Today the only ways to stop a run are Ctrl-C in the terminal that started it (which requires being
that terminal) and killing the process (which takes down every other run in it). A `swarmkit serve`
with three concurrent jobs has no way to stop *one*. A run that is looping through a 40-tool
research turn on a wrong premise burns tokens until its turn cap, and the operator watching it can
only wait.

## The shape: a stop is a deferral without a gate

This is the whole design, and everything else follows from it.

The runtime already parks a run mid-flight and resumes it later: a funnel's `approve` layer raises
`HITLDeferredError`, the graph checkpoints, the job goes `deferred`, and `swarmkit run --resume` /
`POST /jobs/{id}/resume` continues it from the checkpoint
([`gate-state-and-deferring-approval.md`](gate-state-and-deferring-approval.md)).

A stop wants exactly that behaviour with a different *reason*. So:

```python
class RunStoppedError(HITLDeferredError):
    """A human asked this run to stop. Checkpointed and resumable, like any deferral."""
```

A subclass, for the same reason `GateDeferredError` is one: every caller that already knows how to
checkpoint-and-exit on a deferral handles it without changing, and the ones that want to distinguish
a stop can ask.

**No new resume path, no second checkpoint mechanism, no parallel "cancelled run" state machine.**
The cost of getting stop wrong is a run that cannot be resumed, and the way to not get that wrong is
to not write a second implementation of resumption.

## The signal: a durable flag, not a signal or an HTTP call

`stop` has to reach a run in **another process**. The CLI writes `jobs` rows for its own runs
(1.176.0+), serve writes them for its jobs, and one storage service resolves the store for both — so
a column on `jobs` is a channel that already connects every writer to every reader:

```
jobs.stop_requested_at   TEXT   -- ISO timestamp, NULL = not asked
```

`swarmkit stop <run-id>` sets it. The running process reads it. That is the entire transport.

Rejected alternatives:

- **A signal (SIGTERM/SIGUSR1) to a PID.** Needs a pid column, needs the same machine, and cannot
  express "stop this one job" in a process running three.
- **`POST /jobs/{id}/stop` as the only channel.** Works for serve and not at all for a CLI run,
  which is where an operator most often wants it. The HTTP route still exists — but as a *caller* of
  the same flag, not as a second mechanism.
- **An in-memory cancellation token.** Correct for one process, useless across two, and the CLI and
  serve are always two.

## Where it is checked, and what that costs

**At node entry**, in the one `node_fn` every agent node is built from — before the node does any
work, after the previous node's state has been checkpointed by the super-step that produced it.

That placement is what makes the promise honest: everything completed up to the last node boundary
is in the checkpoint, and a resume re-enters at the node that would have run next.

**A stop is therefore cooperative, and it is not instant.** A run inside a 10-minute harness session
or a long MCP call stops when that call returns, not when the operator presses enter. The CLI says
so rather than implying a kill:

```
$ swarmkit stop a46614b1
stop requested for a46614b1 — it will stop at the next agent boundary.
Runs mid-call (a harness session, a slow tool) finish that call first.
```

Pretending otherwise would be worse than the wait: an operator who believes a run is dead and starts
a replacement gets two runs writing the same artifacts.

A second check inside the tool loop (between turns) is a **later refinement**, not v1. It shortens
the wait for the common "40 tool calls on a wrong premise" case, but it also means a stop can land
between a tool call and the model seeing its result, and that interaction deserves its own thinking
rather than being smuggled into this note.

## Status vocabulary

`stopped`, not `deferred` and not `failed`.

- Not `failed`: nothing went wrong, and a reader counting failures should not count this.
- Not `deferred`: `deferred` means *waiting on a human decision that will arrive*. A stopped run is
  waiting on nothing. Collapsing them would make "how many runs are blocked on approvals" —
  a question the review queue exists to answer — silently wrong.
- Not `cancelled`: a cancelled run is over. A stopped run is **resumable**, and the word should not
  suggest otherwise.

`swarmkit run --resume` and `POST /jobs/{id}/resume` both accept `stopped` alongside `deferred`. A
`completed` or `running` job still 409s.

## The idempotence and re-stop rules

- Stopping a job that is not `running` is **not an error** — it is a no-op with a clear message. An
  operator racing a run that just finished should not get a stack trace.
- Stopping an already-stop-requested job re-reports the pending request rather than stacking.
- **A resumed run clears the flag.** Otherwise a run stops, resumes, and immediately stops again on
  the stale request — which reads as a resume that does not work.

## Audit

A stop is a human act against a governed run, so it is recorded like one: `run.stopped` with the
requesting identity, the run id, and the agent boundary it took effect at. "Who stopped the release
run" is exactly the kind of question the audit log exists for, and a stop that only appears as a
status change cannot answer it.

## Surfaces

| Surface | Behaviour |
| --- | --- |
| `swarmkit stop <run-id> [workspace]` | Set the flag; report what will happen and when |
| `POST /jobs/{job_id}/stop` | Same flag, over HTTP; 404 unknown, 409 not running |
| `GET /jobs/{job_id}` | `stop_requested_at` surfaces through the merged view |

The CLI resolves the store from the workspace exactly as `swarmkit logs` and `swarmkit status` do,
so `swarmkit stop` works against a run started by serve on the same store — the two front doors stay
peers.

## Non-goals

- **Not a kill.** There is no `--force` that terminates a process or cancels an in-flight model
  call. A stop that could interrupt mid-call would lose the current node's work, which defeats the
  goal; an operator who genuinely wants that has `kill`.
- **Not stopping a fleet run from the panel.** The flag is per-store; a fleet-wide stop is a
  control-plane feature and belongs with the fleet work.
- **Not stopping a topology.** The stub's help says "gracefully stop a running topology" — but the
  unit that runs, checkpoints, and resumes is a **run**, and the argument has always been a run id.
  The help is corrected to say so.
- **Not a scheduled/conditional stop** (stop after this stage, stop if cost exceeds X). Budget
  enforcement is a separate concern with its own policy questions.

## Test plan

- A flagged run raises `RunStoppedError` at the next node boundary and does **not** run that node.
- The work before the stop survives: the trace and audit events for completed agents are written,
  and the job's usage reflects what was spent.
- A stopped run **resumes** and completes, over both the CLI and `POST /jobs/{id}/resume`.
- Resuming **clears** the flag — a resumed run does not immediately re-stop.
- A run with no flag is byte-identical to today (asserted against a real workspace run, not a mock).
- `stopped` is distinct from `deferred` and from `failed` in the job row and every read surface.
- Stopping a completed job is a no-op with a message, not an error; stopping an unknown run id is an
  error naming the id.
- The audit log records `run.stopped` with the requester.
- The flag reaches a reader: `GET /jobs/{id}` carries `stop_requested_at` (the bug-28 guard already
  asserts every `JobRow` field is reachable — this rides on it).

## Demo plan

A two-terminal transcript: a long run started in one, `swarmkit stop <run-id>` in the other, the run
stopping at the next agent boundary with its completed agents' work intact, `swarmkit logs` showing
the partial trail, and `swarmkit run --resume` finishing the job.

## Open questions

0. ~~**Cache the check?**~~ **No** — see the status note. Resolved by the demo, not by argument.
1. **Should a stop request expire?** A flag set against a run that dies unrecorded stays set
   forever, and a resume clears it — but a run that never resumes leaves a stale row. Probably
   harmless; noting it rather than solving it.
2. **Should the tool loop check too?** See above — it shortens the wait materially for the case that
   motivates the feature, and it needs its own thinking about mid-turn state.
