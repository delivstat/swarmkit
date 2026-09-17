# Harness tool outcomes — a shared vocabulary

Status: implemented (runtime 1.135.0). Follows `executor-abstraction.md` and
`harness-progress-stream.md`.

## The failure this comes from

A design agent produced three runs of confident UI documentation describing screens it had never
seen. The screenshots were referenced by document-relative paths that resolved nowhere inside the
harness sandbox, so the image tool returned nothing. The report's sharpest line:

> There is no error, no warning, and nothing in the trace that says an image was missed. It shows
> `view-screenshot ✓` either way.

That is the same class of bug as the degraded checkpointer, the swallowed `--mcp-config` list and
the overwritten per-stage trace: **information exists, nothing surfaces it, and absence looks like
success.** The tool told us it failed. We threw the answer away and rendered a checkmark.

## Goal

A tool call that failed must be distinguishable, in the trace, from one that succeeded — for every
harness, present and future.

## Non-goals

- Deciding *what to do* about a failed tool call. Retry, abort and repair are policy; this note is
  only about observability. A harness that fails a tool and recovers is still a successful run.
- Reconstructing tool results. We record the outcome, not the payload.
- Per-harness special cases in runtime code. That is precisely what this replaces.

## Why it was broken in four different ways

Each vendor reports outcomes in its own alphabet, on its own event:

| Harness | Where the outcome lives | What it says |
| --- | --- | --- |
| claude-code | a *following* `user` message, as a `tool_result` block | `is_error: true` |
| codex | `exec_command_end` | a numeric exit code |
| gemini-cli | `tool_call_response` | `success` / `error` |
| opencode | the same `tool` event as the invocation | `completed` / `error` / `pending` |

Three of the four adapters mapped no outcome at all — they recorded that a tool was *called*.
opencode mapped one, and passed its vendor's raw word straight through to `ExecToolCall.status`,
which was an unconstrained `str` that nothing downstream interpreted.

So there was no single place to fix. A naive per-adapter patch (`status != "ok"` ⇒ error) would
have flagged every healthy opencode call in existence, since opencode says `completed`.

## Design

**One normalization, at the seam every adapter already passes through.**

`ExecToolStatus = Literal["", "ok", "error"]`, with `normalize_tool_status()` applied in
`_event_map._build_event` — the single construction site for `ExecToolCall`. Adapters keep emitting
whatever their vendor says; the vocabulary collapses once, before any consumer sees it.

The three-value vocabulary matters more than the two obvious ones:

- `""` is **unknown**, and is deliberately not `ok`. A harness that reports no outcome, a call still
  in flight, and a word we have not learned all land here. Reading silence as success is the exact
  conflation that produced the original bug; reading it as failure would cry wolf on every
  in-flight call.
- Unrecognized vocabulary normalizes to unknown rather than to either pole. A new harness is
  under-reported, never wrongly reported.

`_harness_node` then sets `ToolCall.error` when — and only when — the status is `error`. It knows
nothing about any vendor.

### Adapters stay data

codex reports exit codes; gemini reports an enum that is not the run-status enum. Both need a
translation table, and the DSL had exactly one, hardcoded by name:

```python
table = status_map if spec["map"] == "status_map" else {}   # before
table = maps.get(str(spec["map"]), {})                      # after
```

Any top-level `*_map` block is now a named table an emit can reference. This is the invariant from
`feedback_executors_are_data` holding: teaching the runtime a fifth harness's vocabulary must be a
YAML edit, not a Python release. The schema allows `^(?!event_map$)[a-z][a-z0-9_]*_map$` — the
exclusion because `event_map` ends in `_map` but is the rule list, not a lookup table.

### The other half of the same line

`ToolCall.result_length` — documented as the length of the *result* — was being given
`len(event.input_summary)`, the length of the *arguments*. A `view_image` that returned nothing
recorded a healthy-looking number purely because the path was long. The number was not missing; it
was wrong in the reassuring direction. Arguments now go to `arguments`, where they belong.

## Test plan

`packages/runtime/tests/test_harness_tool_outcome.py`:

- the vocabulary — every way a harness says worked / failed / still running, plus the safety
  property that an unknown word is unreported rather than assumed good
- one failing and one succeeding tool call **per bundled harness**, in that harness's own native
  protocol, asserting the failure surfaces and the success is not cried wolf over
- no bundled adapter leaks a raw vendor word past the seam
- an invented harness can name its own translation table with no runtime change
- `status_map` still resolves after generalization
- a failed and a successful call, with identical inputs, produce distinguishable trace records

Schema fixtures: `executor-adapter/named-maps.yaml` (valid, three tables) and
`executor-adapter-invalid/named-map-not-a-table.yaml`.

## Demo

`just demo-harness-tool-outcomes` — feeds one failing tool call per bundled harness, in each
harness's native protocol, and prints what the trace would record before and after.

## What this does not fix

The image bug had a second, independent half: relative paths do not resolve inside the harness
sandbox, and MCP `ImageContent` is the only channel by which an image reaches the model at all.
This change makes that failure *visible*; it does not make the path resolve. That is separate work.
