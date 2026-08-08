# A bound funnel actually runs its layers

**Status:** implemented — swarmkit-runtime 1.172.0.

## Goal

An agent that declares `funnel: <id>` gets the quality gate it declares, on the path serve and the
CLI actually use.

## What was wrong

Two holes, one behind the other.

**1. The gate never wrapped.** `compile_topology` wrapped an agent's node only when
`agent.funnel is not None and review_queue is not None`, and nothing in the runtime ever passed a
review queue. `WorkspaceRuntime.compile()` — the entry point for serve and the CLI both — passed ten
kwargs and neither `review_queue` nor `role_registry` was among them. The guard was `False` on every
run either has ever made. A declared funnel was resolved, attached, validated and inert.

The guard was also on the wrong dependency. The review queue is read by exactly one thing in
`_gate_funnel`: the multi-party approver. `validate` needs nothing; `judge` needs only governance.
Three layers that touch no queue were gated behind the one that does.

**2. A schema-only `validate` wired nothing either.** `build_deterministic_validator` returned `None`
unless `slice_budget` or `cited_change` was configured, documented as "a schema-only validate stays
handled by output governance". It was not: `output_schema` is merged from the agent and its
archetype only (`_merge_output_schema` in the resolver), never from the funnel, and nothing bridged
them. So `validate: {schema: ...}` — the ordinary spelling — produced no check anywhere. Three
consecutive specs shipped with `code_changes` entries whose `kind` (`config`) and `action` (`modify`)
are not in their own schema's enums, read and approved by a human against a contract nothing
enforced.

## What ships

- The compiler wraps on `agent.funnel is not None` alone.
- `build_deterministic_validator` gains a `schema` check, resolved against the **funnel** that
  declared it (as `output_schema` resolves against its declaring artifact) and validated with the
  existing `validate_all_skill_output`. Parsing is tolerant (`extract_json_object`): a model that
  fences its JSON produced a conforming artifact badly presented, and the critique goes back to the
  drafter as a retry instruction, where "not valid JSON" would send it to fix the wrong thing.
- An unreadable `validate.schema` warns and the rest of the funnel still runs. A configuration error
  the operator must see, not a layer that quietly disappears and not a dead run.
- On retry exhaustion the artifact **proceeds** with the failure in provenance and in the audit log.
  A declared contract the output violates is worth a rewrite and a record; turning every
  currently-passing pipeline into a failing one is not what a quality gate is for.
- `provenance` now carries `validate_ok`, the one layer result the bundle omitted — a reader could
  not tell a passed schema check from an absent one.

## Human approval is deliberately not on this path

`approve` is the sole predecessor of END — the invariant that stops an advisory layer deciding — so
a gate that runs needs an approver. In-node there is nothing to park a human in: `resolve_multiparty`
polls the review queue with `_DEFAULT_MAX_WAIT_SECONDS = 7 * 24 * 3600`, inside the agent's
coroutine, holding the model session, losing the wait entirely on a serve restart. A `swarmkit run`
from a terminal could not approve at all. Wiring the deps as the bug report first suggested would
have converted an inert quality gate into a hung run.

Human approval on the pipeline path already exists and works properly: the stage-level `gate:`,
which parks the saga durably and survives restarts. An in-node approve would duplicate it, worse.

So `build_advisory_approver` records and passes. It is not silent: every run emits
`funnel.advisory_completed` naming the failed layers, the retry count, and `approve: "deferred to
the stage gate"`. That statement lives in the audit record rather than a warning because `approve`
is a **required** property of the Funnel schema — every funnel has one, so a warning would fire on
every compile of every gated topology and mean nothing. The compile-time notice is INFO.

## Non-goals

- Not a `review` layer. `run_agent_funnel_gate` builds no reviewer at all, so `review:` remains
  unwired on both bindings; that is a separate gap, unchanged here.
- Not a change to `StageRunner`, which built its own queue and already ran funnels.
- Not a new store, endpoint or schema field.

## Test plan

`packages/runtime/tests/test_funnel_layers_actually_run.py` drives `WorkspaceRuntime.compile()`, not
`compile_topology` — the missing kwarg *was* the bug, and every existing funnel test passed while the
feature was unreachable.

The judge is evaluated; a failing judge sends the draft back; a below-threshold confidence fails; a
schema-violating artifact is routed back and the rewrite survives; a conforming one is untouched;
fenced JSON is not a schema failure; retry exhaustion proceeds with the failure audited; the audit
record names the failed layer and the deferral; a declared `approve` does not wait for a human; and
an unreadable schema warns while the rest of the funnel still runs.

## Demo

```
$ uv run pytest packages/runtime/tests/test_funnel_layers_actually_run.py -q
............                                                             [100%]
12 passed
```

A validate failure, previously invisible, now on the run's own log:

```
funnel gate on 'design'/'designer': validate failed after 1 retries — the artifact proceeds with
the failure recorded. Human approval is the stage-level `gate:`, not this layer.
```

## The follow-up this leaves

`review:` is declared in the Funnel schema and built by neither binding. It is the same defect shape
as this one — configuration accepted, validated, displayed and loaded by nothing — and it is now the
last layer in that state.

That recurrence (bugs 21, 22, 23, 25) is the real argument for the check the reports keep asking
for: at startup, report every declared binding that no code path can reach. Four individual fixes
have not stopped the fifth.
