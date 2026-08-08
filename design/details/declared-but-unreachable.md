# Reporting configuration that no code path can reach

**Status:** proposed — design only.

## Goal

Make "declared, accepted, validated, displayed, and loaded by nothing" a reported condition instead
of a bug report.

## Why this and not a fifth individual fix

Five defects in a row have had the same anatomy:

| bug | declared | what consumed it | how it was found |
| --- | --- | --- | --- |
| 21 | `memory-reader` at `pre_input` | ran, but could not see governed memory | a curated fact never appeared |
| 22 | `required: false` bindings | `merge_decision_skills` discarded them | reading the merge |
| 23 | every decision-skill binding | `Trigger` was a plain Enum, so no trigger point ever selected one | three bug reports deep |
| 25 | `funnel:` on an agent | the compiler's guard required a `review_queue` nobody passed | a $1.82 stage with no judge line |
| 25b | `validate: {schema: ...}` | `build_deterministic_validator` returned `None` for it | the same investigation |

Every one passed validation. Every one was displayed by `swarmkit validate`. None errored. In each
case the operator believed a check was enforcing something for weeks or months.

The individual fixes were all correct and none of them prevented the next. What they have in common
is not a shared line of code — it is the absence of anyone asking *is this reachable?*

## The part that cannot be done statically

It is tempting to build one check. The five split into three classes and only the first is
statically decidable:

**A. Broken at wiring** — the configuration never reaches its consumer. Bugs 22 and 25. The
consumer's guard is `False`, or the merge dropped the entry, so nothing is ever constructed. A
compile-time check sees this: the thing was declared, and the compiler built nothing for it.

**B. Broken at selection** — wired correctly, and the predicate that selects it is never true. Bug
23. The binding *was* attached to the node; `b.trigger == "pre_input"` was simply always `False`.
Nothing at compile time is wrong. **No static check can catch this**, and pretending otherwise is
how this design would ship as the sixth instance of the same defect.

**C. Broken at capability** — it ran, and could not see what it needed. Bug 21. Only a behavioural
test catches this. Out of scope here; named so the boundary is explicit.

So: two mechanisms, for A and B, and an honest statement that C is not covered.

## Mechanism 1 — a wiring ledger (class A)

**Not** a hand-maintained registry of "which code consumes which field". That is another declaration
that goes stale, and — the decisive argument — it would have been *wrong* for all four bugs: it
would have said `funnel` is consumed by `_compiler.py`, which is exactly what everyone believed.

Instead, instrument the wiring itself. A collector is active for the duration of `compile()`, and
every site that wires a declared thing records it **on the line where it wires it**:

```python
if agent.funnel is not None:
    node_fn = _wrap_with_funnel_gate(...)
    ledger.wired("funnel", funnel.id, agent=agent.id)
```

Then diff: declarations enumerated from the *resolved workspace* (independent of any consumer)
against what the compiler actually built.

The property that makes this work is that the claim and the act are the same statement. A ledger
call cannot report a wrap that did not happen, because it sits inside the branch that does the
wrapping. Under bug 25 the branch never ran, so the ledger would have been empty and the diff would
have printed `funnel spec-review declared on designer — nothing wired it`.

**Where it can go wrong, and which way.** A new wiring site that forgets its ledger call produces a
false "unreachable" report. That is the safe direction — noisy rather than silent — and it is the
opposite of the failure this whole document exists to prevent. Two mitigations: put the ledger call
inside the shared helper wherever one exists (so a new caller inherits it), and a guard test that
every branch reading a declared field records something, in the spirit of the existing
`test_llms_txt_published_copy`.

**Scope of "declared thing".** Start with the two families that have actually burned us — decision
skill bindings (workspace + topology, per trigger, per agent) and funnel layers (`validate`,
`judge`, `review`, `approve`, per bound funnel) — with the enumerator written as a registry that new
kinds join. Deliberately not every field in every schema: a check that reports fifty uninteresting
things is a check nobody reads, which is the failure mode of the thing it replaces.

`review:` is the first customer. It is declared in the Funnel schema and built by neither binding
today, so a correct implementation must report it on day one. That is the acceptance test.

## Mechanism 2 — the inert-binding report (class B)

Wired is not fired. The second half asks the audit log a different question: **which declared
bindings have never once produced an event?**

The events already exist — `skill.executed`, `funnel.advisory_completed`, the decision-skill
evaluations — so this is a query, not new instrumentation.

The subtlety is the denominator. "Zero evaluations" means nothing without knowing whether the
trigger point was ever reached. Bug 25's report got this right by hand and it is worth copying
directly: it used `memory context injected: 2` as a control, so that `spec-judge: 0` in the same log
was a real negative rather than a logging gap. Formalised, the report is a ratio —

```
memory-reader     pre_input     bound on 3 topologies    0 evaluations / 47 applicable runs
spec-conformance  post_output   bound on wms-design      0 evaluations / 12 applicable runs   REQUIRED
```

— where the denominator is completed runs of topologies the binding applies to. A `required: true`
binding at 0/12 is the loudest line the system can print, and it is exactly the line bug 23 would
have produced months before anyone noticed.

This mechanism is retrospective by nature: it needs runs to have happened. That is the cost of
catching a class that static analysis cannot, and it is why both mechanisms ship, not one.

## Surfaces

Following `gate_coverage` — a shared pure function, thin interfaces over it:

- `swarmkit_runtime.reachability` — `compute_reachability(workspace, ledger)` and
  `compute_inert_bindings(workspace, audit_store, since=...)`, both pure and both returning
  dataclasses with a `to_dict`.
- **`swarmkit validate`** — grows a reachability section, always on. This is the primary home: the
  complaint in every one of these reports is that `validate` accepted and displayed the binding.
- **`swarmkit serve` startup** — logs the summary once; WARNING when anything is unreachable, and
  named rather than counted.
- **`GET /workspace/reachability`** — for the UI, matching `GET /pipelines/{id}/gate-coverage`.
- **`--require` on validate** — non-zero exit for CI, matching `gates --require`. Off by default:
  turning existing green pipelines red on upgrade is not this feature's job.

The inert-binding report is retrospective and belongs with the other audit-log reads — a
`swarmkit comprehension`-shaped command (`swarmkit inert`, or a flag on `gaps`), not on the
startup path.

## Non-goals

- Not a general dead-config linter over every schema field.
- Not a replacement for behavioural tests. Class C is not covered, and a passing reachability report
  means "something was built for this", not "it works".
- Not a policy engine. It reports; it does not refuse to start.

## Test plan

The four historical defects, reconstructed as fixtures, each asserted to be *reported*:

- a topology binding with `required: false` that the merge drops (bug 22);
- an agent with `funnel:` compiled with the guard restored to its broken form (bug 25);
- a funnel declaring `validate: {schema: ...}` where the validator builder returns `None` (bug 25b);
- a funnel declaring `review:` — which needs no reconstruction, because it is unwired today;
- and, for mechanism 2, an audit store with a `required: true` binding at 0/N.

Plus the negatives that keep it usable: a fully wired workspace reports nothing, and a binding
scoped to an agent that the run never reaches is not called unreachable.

Then the meta-test, because this check is itself configuration that could go inert: a guard asserting
every wiring site records to the ledger.

## Demo

`swarmkit validate` on the reporter's own workspace, which should print — before any code changes to
the funnel path — the `review:` layer as unreachable, and after reverting bug 25's fix locally, the
funnel itself.

## Open questions for review

1. **Does `validate` fail by default once this is accurate?** I lean no, per above — report always,
   exit non-zero only under `--require`. But an unreachable `required: true` binding is arguably a
   broken workspace and not a warning.
2. **Is the inert report a new command or a flag?** `swarmkit inert` is discoverable; a flag on
   `gaps` keeps the command surface small. Weak preference for the flag.
3. **How far does the declaration registry go in v1?** Decision skills and funnel layers are the
   demonstrated need. MCP grants, executors and `output_schema` are plausible next entries and each
   adds enumeration code with no evidence yet that it is inert anywhere.
