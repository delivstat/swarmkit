# Decision skills on a harness executor

Status: implemented (runtime 1.142.0). Closes gap #2 in `docs/notes/harness-parity-gaps.md`.

## The failure

A topology binds a `post_output` decision skill with `required: true`. On an agent whose
`executor.kind` is `harness`, the skill is **never invoked**. The output is returned unchanged,
unvalidated and unannotated, and the run reports success.

`node_fn()` hands off to the harness runner with an early `return`, and every decision-skill gate
sits after it:

```python
if agent.executor.kind != "model":
    return await run_harness_node(...)     # every gate below is unreachable

if _ds_bindings:                            # pre_input
if _ds_bindings:                            # post_output
```

`_ds_bindings` was computed for the agent and then discarded. `run_harness_node()` had no parameter
to receive it either.

Observed on `wms-design`: the agent returned a markdown document where the topology required a JSON
object. `spec-conformance` would have returned `verdict: fail` and triggered a revision. It did not
run, and the markdown became the run's final output.

## Why it is costly

The failure is silent and inverted from the safe direction:

- `required: true` reads as "this gate must pass". On a harness it meant nothing.
- `swarmkit validate` reports no error — the binding is structurally valid.
- The trace shows a normal successful node. There is no "skipped" marker to notice.
- It is **executor-dependent**: a topology validated on a model node changes behaviour when
  switched to `executor.kind: harness` with no other edit and no warning.

Compounding, `output_schema` is also ignored on the harness path (gap #3), so a harness agent had
neither a schema constraint nor a post-hoc check — the two independent mechanisms that would each
have caught a non-conforming output.

## Scope: which triggers actually apply

The report says all four trigger points are affected. That is true in the sense that none ran, but
only two are *reachable* for a harness node:

| Trigger | Fires in | Applies to a harness node? |
| --- | --- | --- |
| `pre_input` | `node_fn`, before execution | **yes** |
| `post_output` | `node_fn`, after execution | **yes** |
| `checkpoint` | `_task_executor`, between task-plan rounds | no — a harness node builds no task plan |
| `pre_synthesis` | `_task_executor`, before synthesis | no — same reason |

So this change wires `pre_input` and `post_output`. `checkpoint` / `pre_synthesis` remain
model-path-only because they are properties of structured delegation, not of the executor. Claiming
otherwise would be claiming a fix that does nothing.

## Design

**Run the gates for every executor kind**, by restructuring `node_fn` so the harness result flows
into the shared post-output path rather than returning early.

1. **`pre_input` moves above the executor dispatch.** It is a relevance gate on the *input*, which
   is executor-agnostic, and it must run before the harness launches — refusing after paying for a
   harness run would be a strange way to decline. Workspace-memory context injection moves with it,
   for the same reason: the harness must see the injected context.

2. **`post_output` runs on the harness result**, using the same `evaluate_post_output` as the model
   path, and the revised text replaces the node's `output`, `agent_results` and message so the
   revision is what flows downstream — not merely logged.

3. **A failed harness is not gated.** When the harness reports a failure (the `node_errors` marker
   added in 1.139.0), the result is returned as-is. Gating it would ask a decision skill to judge an
   error string, and a `required` skill would then trigger bounded retries of something that failed
   for infrastructure reasons — burning real money re-running a harness whose sandbox could not
   start. A failure is a failure, not a non-conforming output.

### The retry, driven by the agent's own executor

`_make_retry_fn` re-prompts a **model** with the previous output plus the feedback: "the agent
doesn't re-run tools — it revises using data it already has." That is wrong for a harness twice
over. It needs a `model_provider` a harness agent may not have; and a harness's output is the
product of work in a sandbox, so revising its *text* with a different model would produce a
description of a fix rather than the fix.

So a harness retry **re-invokes the harness**, with the decision skill's feedback appended to the
task statement — the same revision loop, driven by the executor the agent actually uses.

This is honestly more expensive than the model retry: each attempt is a full harness run. That cost
is bounded by the binding's existing `max_retries` (default 1, and `0` disables retry entirely),
and it is the only thing that can actually produce a corrected artifact. A cheap retry that cannot
fix anything is worse than none.

## Alternative considered and rejected

The report offers a fallback: refuse the combination at validate time — fail a topology binding a
`required: true` decision skill to a harness agent. That is a real improvement over silence, but it
is a worse outcome than making the feature work, and it would have to be undone by this change
anyway. Rejected in favour of the fix; kept in mind as the answer had the retry loop proved
infeasible.

## Test plan

`packages/runtime/tests/test_harness_decision_skills.py`:

- a `post_output` skill runs on a harness node (the bug)
- a `pre_input` skill can refuse before the harness ever launches — asserted by the harness *not
  being invoked*, since the cost is the point
- the revised text replaces the node's output, agent_results and message
- a `required` skill's failure triggers a retry that **re-invokes the harness**, with the feedback
  in the task statement
- `max_retries: 0` disables retry; retries are bounded
- a **failed** harness result is returned ungated and un-retried
- the model path is unchanged (a regression guard, since `pre_input` moved)
- no bindings ⇒ no behaviour change at all

## Demo

`just demo-harness-decision-skills` — the `wms-design` shape: a harness agent returning markdown
where JSON was required, with the gate off and on.
