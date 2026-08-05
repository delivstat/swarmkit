# A chat turn is recorded, and its audit trail is findable

**Status:** implemented (runtime 1.155.0)

## Goal

Make a conversation turn as observable as any other topology run: a job row in history, and an
audit trail reachable from the conversation that produced it.

## Non-goals

- No change to how a turn executes. Context building, history, and streaming stay as they are.
- No conversation-level aggregate row. A turn is the unit of work, and the conversation is the
  `correlation_id` that groups them — the same shape as a pipeline run and its stages.

## The gap

Chat was the last topology run recording nothing:

| how a topology runs | job row | run id |
| --- | --- | --- |
| `POST /run/{topology}` | yes | minted by `JobService` |
| `swarmkit run` (1.150.0) | yes | the thread id |
| a pipeline stage (1.152.0) | yes | `<correlation>:<stage>` |
| **a chat turn** | **no** | **a fresh random UUID** |

Measured before the change, on a two-agent run:

```
JOB ROWS:   0
AUDIT ROWS: 2
   agent.completed  run_id=600c57ae-c390-420c-bcb4-8dd0c7bf6ae9
   agent.started    run_id=600c57ae-c390-420c-bcb4-8dd0c7bf6ae9
```

Two separate problems, which look like one from the outside:

1. **No job.** A conversation never appeared in `/jobs`, and its token cost was attributable to
   nobody. Chat is the v1.0 on-ramp, so this was the most-used path and the least recorded one.
2. **Audits written but unreachable.** `ConversationManager.send` called `runtime.run` *without a
   `thread_id`*, so each turn's events — and its trace file — landed under a fresh random UUID that
   nothing referenced. The record existed and could not be reached from the conversation that
   caused it, which for a reader is the same as not having it.

The second is the more instructive failure: auditing was never broken, only unfindable. A count of
audit rows would have looked healthy.

## Design

**One id per turn: `<conversation>:<n>`.** Per-turn rather than per-conversation for the same
reason a pipeline stage gets its own — the id is the LangGraph checkpoint thread *and* the trace's
`run_id`, and a trace saves to `{run_id}.json`. One id per conversation would make each turn
overwrite the previous turn's trace and inherit its graph state. A turn is already given the
history as text, so it needs no checkpoint continuity.

**Numbered by exchange, not list position.** `turns` holds both sides, so positions would run
1, 3, 5 and read as gaps in a record that has none.

**The conversation is the `correlation_id`.** `GET /jobs/history?correlation_id=<conversation>`
returns that chat and nothing else, reusing the column added for pipeline stages in 1.152.0.

**The store comes from the runtime.** A new `WorkspaceRuntime.store` property returns
`self._storage().store()` — the one storage service (`design/details/storage-service.md`). Both
front doors (`swarmkit chat` and `POST /conversations/{id}/messages`) go through
`ConversationManager`, so recording sits there once rather than in each caller.

**`BaseException`, not `Exception`, around the run.** A Ctrl-C mid-answer must still close the row;
otherwise an interrupted chat leaves a row at `running` forever — the stalled shape. The exception
still propagates: recording a failure must not swallow it.

## Surface

No new endpoints. Chat turns now appear in `/jobs`, `/jobs/history`, and `/jobs/{id}` (openable
since 1.154.0), and their audit rows answer `GET /audit?run_id=<conversation>:<n>`.

## Test plan

`test_chat_records_a_job.py` — the row exists and is linked, the run gets a thread id, turns are
numbered by exchange and each gets its own, output and usage are recorded, failed and interrupted
turns close their rows, and neither a missing nor a broken store costs the answer.

## Demo

`packages/runtime/demos/chat_records_jobs.py` — two real turns against a mock model, printing the
job rows and the audit run ids.
