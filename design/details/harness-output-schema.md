# `output_schema` on a harness executor

Status: implemented (runtime 1.143.0). Closes gap #3 in `docs/notes/harness-parity-gaps.md`;
companion to `harness-decision-skills.md`.

## The gap

`_harness_node.py` contained **zero references** to `output_schema`. A harness agent therefore had
neither a schema constraint nor — until 1.142.0 — a post-hoc decision-skill check: the two
independent mechanisms that would each have caught a non-conforming output, both absent on the same
path.

That is why `wms-design` could return a markdown document where the topology declared a JSON object,
and the run reported success.

## Design

Validate the harness result against its declared schema before the decision-skill gate, and correct
**through the harness**.

The correction loop mirrors the model path's `_validate_and_correct`: parse, collect *all* field
errors, send them back as a targeted correction, bounded retries. What differs is who performs the
correction. On the model path a re-prompt is cheap and sufficient. On a harness the output is the
product of work in a sandbox, so a model asked to fix the JSON would be editing a description of
work it cannot reach — the same reasoning as the decision-skill retry, and the two now share one
`_reinvoke` helper.

On exhaustion the text passes through **annotated**, and an `output.schema_violation` audit event is
recorded. The gap being closed was output that failed a declared contract and looked fine; replacing
it with output that fails a contract and looks fine *in a different way* would not be a fix.

### Only an explicitly declared schema

The model path uses `get_effective_output_schema`, which falls back to the worker platform default —
`{findings: [{fact, source}], not_found, raw_data}` — for any `role: worker` with no explicit
schema.

**That default must not apply here.** `examples/sdlc-pipeline` alone has a `developer` archetype that
is `role: worker` + `kind: harness` with no `output_schema`, and it produces a diff, not findings.
Applying the default would make every run of it fail validation and burn *full harness retries*
against a contract nobody wrote — a silent, expensive regression introduced by a change meant to
increase safety.

The platform default exists for structured inter-agent communication between model workers in the
delegation pattern. A harness node produces artifacts. So this path enforces what the author
actually declared, and nothing more. Two archetypes in that same example (`architect-reviewer`,
`expert-reviewer`) *do* declare schemas — those are the ones that start being enforced.

### Ordering

Schema first, then decision skills. A `required` skill should judge output that already satisfies
its declared shape, rather than spending a retry — and a full harness run — on a shape violation the
schema layer can name exactly.

## Revising an earlier recommendation

I previously suggested the funnel `validate:` layer as the home for this, "so one implementation
covers every executor kind". Two things changed my mind:

1. **A funnel does not cover `swarmkit run <topology>`.** That is the same limitation already noted
   for the decision-skill workaround: it covers pipeline execution and nothing else.
2. **`output_schema` is declared on the agent.** A contract declared at the node belongs enforced at
   the node, whichever executor runs it — that is the executor-abstraction invariant, and it is what
   makes `executor.kind` a mechanism choice rather than a contract change.

With `_run_harness_with_gates` already carrying an executor-driven retry, the node is now the
cheaper place as well as the correct one.

## Test plan

`packages/runtime/tests/test_harness_output_schema.py`:

- a declared schema is enforced; the correction names the offending fields and keeps the task
- conforming output is untouched (no wasted harness run)
- exhausted retries annotate **and** emit an auditable `output.schema_violation`
- the corrected text replaces output, `agent_results` and the message
- a `role: worker` with no schema gets **no** platform default (the regression guard)
- an explicit opt-out wins; no schema means no validation round
- a failed harness is not schema-checked (validating an error string would burn retries trying to
  make it parse)
- schema runs before the decision skills

## Demo

`just demo-harness-output-schema` — a harness agent returning markdown against a declared schema,
corrected through the harness.
