---
title: Live progress from a running harness
description: A harness node consumes the executor's event stream into local buffers and emits nothing until the run ends, so a multi-minute claude-code run is silent on both the CLI and serve's SSE endpoint. This adds a per-run progress sink — the ContextVar idiom already used for the active trace — carrying a safe summary by default and the verbose text only to a local subscriber.
tags: [runtime, executors, observability, cli, serve]
status: draft
---

# Live progress from a running harness

**Scope:** `runtime` (`langgraph_compiler/_harness_node.py`, a new `progress` module), `cli`
(`swarmkit run`), `server` (`_jobs.py`)
**Design reference:** §14.5 (observability), §6.2/§6.3 (harness interaction). Builds on
`executor-abstraction.md`, `executor-relay-plan.md`.
**Status:** draft

## Goal

Make a running harness observable while it runs — on the CLI and through serve's existing SSE
stream — without putting its full output into a shared store by default.

## Non-goals

- **Not a new transport.** serve already streams `job.events` over SSE; this gives that stream
  something to say.
- **Not replacing the trace.** The trace stays the after-the-fact record. This is the during.
- **Not streaming model-node tokens.** The model path has its own `--verbose` output; widening that
  is a separate question.
- **Not persisting progress.** Progress is ephemeral by design — the durable record is the trace
  and the audit.

## The problem: the events exist and are thrown away

`_harness_node.py` already consumes a live stream:

```python
async for event in enforce_budget(stream, budget, cancel=_cancel):
    ...
    elif isinstance(event, ExecMessage):
        round_messages.append(event.text)          # buffered, never emitted
    elif isinstance(event, ExecToolCall):
        meter.tool_calls.append(ToolCall(...))     # for the trace, after the fact
```

There is no logger call, no callback and no sink in that loop. Every event terminates in a local
list. And serve's `/jobs/{id}/stream` can only relay `job.events`, which is appended to in exactly
four places — job started, completed, timed out, failed — none of them during execution.

So a 226-second harness run is silent on **both** surfaces, by construction rather than by
misconfiguration. There is no flag that turns it on. The only in-flight signal that a run is alive is
that the process has not exited, which is also what a hung run looks like.

That is the same failure shape as the bugs this codebase has been clearing all release: the
information exists, nothing surfaces it, and the absence is indistinguishable from "nothing is
happening".

## The design

### 1. A per-run progress sink, as a ContextVar

The same idiom as `_active_trace_var` in `_compiler.py`, and for the same reason: asyncio copies the
context when a task is created, so concurrent runs under one `swarmkit serve` each see their own
sink instead of clobbering a module global.

```python
# progress.py
@dataclass(frozen=True)
class ProgressEvent:
    agent_id: str
    kind: Literal["started", "tool", "message", "usage", "interaction", "finished"]
    summary: str          # always safe to publish — no harness output verbatim
    detail: str = ""      # may contain harness text; local subscribers only
    at: datetime = ...

ProgressSink = Callable[[ProgressEvent], None]

def set_progress_sink(sink: ProgressSink | None) -> None: ...
def emit_progress(event: ProgressEvent) -> None: ...   # no sink installed ⇒ no-op
```

Threading a callback through `runtime.run → compile → node factory → run_harness_node` would touch
five signatures for a thing only two callers install. The ContextVar keeps the seam at one line in
the event loop.

**Emission is best-effort.** A sink that raises must not fail a run — the same rule already applied
to the OTel mirror and to usage recording. A misbehaving subscriber degrades observability, never
the work.

### 2. `summary` vs `detail` — the exposure split

`ExecMessage.text` is the harness's full assistant output. For claude-code that is verbose and can
contain file contents, and file contents are where a credential shows up. Publishing it to
`job.events` would put it in the job store and over HTTP to anyone holding `serve:read` — the
exposure question already settled for traces in `traces-in-the-store.md`, arriving again.

So each event carries both, and the subscriber chooses:

| kind | `summary` (safe) | `detail` (verbose) |
| --- | --- | --- |
| `tool` | `Read(app/Foo.java)` — tool name + short target | the full input summary |
| `message` | first line, truncated to 120 chars | the full text |
| `usage` | `12.4k in / 3.1k out · $0.21` | — |
| `interaction` | `awaiting approval: Bash(deploy)` | the rationale |

**serve publishes `summary` only. The CLI under `--verbose` prints `detail`.** That is not a
half-measure: a local terminal already has the workspace and the credentials; a shared job record
does not, and the difference in blast radius is the whole point.

### 3. Two subscribers

- **CLI** (`swarmkit run --verbose`): print progress as it arrives. Converts a multi-minute blackout
  into a readable trail of tool calls.
- **serve** (`_jobs.py`): append `summary` to `job.events`. The existing SSE endpoint relays it with
  **no change** — `/job` and `/runs` gain live harness progress for free.

Without `--verbose` the CLI stays quiet, which preserves today's default for scripts that parse
stdout.

## API shape

```python
from swarmkit_runtime.progress import ProgressEvent, emit_progress, set_progress_sink

# _harness_node.py — one call per event kind, inside the existing loop
emit_progress(ProgressEvent(agent_id, "tool", f"{event.tool}({short})", detail=event.input_summary))

# _jobs.py
set_progress_sink(lambda e: job.events.append(f"[{e.agent_id}] {e.summary}"))

# _cmd_run.py, under --verbose
set_progress_sink(lambda e: typer.echo(f"  {e.summary}" + (f"\n    {e.detail}" if verbose_full else "")))
```

## Test plan

- **Unit.** `emit_progress` with no sink is a no-op; a sink that raises does not propagate; the
  ContextVar isolates two concurrent runs (assert each sink sees only its own events).
- **The regression.** Drive `run_harness_node` with a scripted event stream and assert progress is
  emitted **during** the stream, not after — the whole defect is that it arrived only at the end, so
  a test asserting "events were emitted" would pass against the broken code. Assert ordering against
  a stream that yields between events.
- **Exposure.** A `message` event whose text contains a secret publishes a `summary` that does not
  contain it; `detail` does. Asserted on the emitted event, not on the formatter.
- **serve.** A job running a harness accumulates `job.events` beyond the four lifecycle lines, and
  `/jobs/{id}/stream` relays them.
- **CLI.** `--verbose` prints tool calls; without it, stdout is unchanged.

## Demo plan

`just demo-harness-progress` — a scripted adapter (no real harness, no API spend) emitting a
message/tool/usage sequence with delays, shown twice: `swarmkit run --verbose` printing the trail
live, and a `curl` of `/jobs/{id}/stream` showing the same run's summaries arriving before the job
completes.

## Open questions

- **Should progress be rate-limited?** A chatty harness could append hundreds of lines to
  `job.events`, which is held in memory per job. A cap (or a ring buffer) is probably wanted, with
  the same posture as the stderr tail: bounded, and say when truncated.
- **Does the fleet panel want this?** Federating live progress across instances is a different
  problem from federating summaries, and probably wants a decision before it is assumed.
- **Model nodes.** The same sink could carry model-path progress and unify `--verbose`, which is
  currently a separate print path. Deliberately out of scope here, but the seam should not preclude
  it — hence `agent_id` on every event rather than anything harness-specific.
