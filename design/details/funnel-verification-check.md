# Funnel verification strength — design

Answers open question 3 of `design/details/extracting-the-pipeline.md`: what replaces
`swarmkit gates --require` for CI.

## Goal

Tell a workspace author, before a run, **which agents produce an output that nothing checks** — and
let CI fail on it.

`gate_coverage` answered "what is the narrowest verified edge of this pipeline" by classifying stage
edges against their funnels. The stage graph left with the bundled sequencer, so the edge analysis
went with it. The question underneath was never about pipelines: *which agents produce an artifact,
and how strongly is it checked* is about topologies and funnels, both of which stay. It is the
natural sibling of the reachability report — *"this run's output is verified by nothing"* is the same
class of finding as *"this binding is reached by nothing"*.

## Non-goals

- Not a re-implementation of stage-edge coverage. There are no stage edges to cover.
- Not a run-time check. This reads the compiled workspace; it does not observe a run.
- Not a quality judgement of the layers themselves — a weak judge and a strong judge both score 1.

## Shape

`swarmkit_runtime/verification.py`, pure and read-only:

```python
compute_verification(workspace: ResolvedWorkspace, ledger: WiringLedger) -> VerificationReport
```

Two properties carry the design.

**Strength counts wired layers, not declared ones.** A funnel declaring `validate` whose builder
returned nothing contributes nothing, because it does nothing — counting the declaration would make
this check commit the exact defect the reachability report exists to catch. The ledger is the same
one `compute_reachability` reads, from the same compile, so the two reports cannot disagree.
Declared-and-unwired layers are still reported, as `inert`.

**Only roots are findings.** Every agent's strength is reported, but a leaf worker returning a fact
to its parent is not producing a reviewable artifact, and flagging every one would make a report
nobody reads. The root's output *is* the run's output: what a caller acts on, what a gate approves,
what a downstream stage would have consumed.

## Surfaces

- `WorkspaceRuntime.verification()` — shares the reachability compile pass.
- `swarmkit validate` — a verification section, always printed.
- `swarmkit validate --require-verified` — exit 1 when any root is unverified. Kept separate from
  `--require` (reachability): *"is my config wired"* and *"is my output checked"* are different
  questions a CI job may want independently, and folding them would break existing `--require` jobs.
- `GET /workspace/verification`.

The gate is strict and opt-in. Narrowing it to roots declaring an `output_schema` was considered and
rejected on evidence: 0 of 3 roots in the reference workspace and 0 of 15 in `sdlc-pipeline` declare
one, so the signal cannot distinguish an artifact-producing root from any other.

## Test plan

`packages/runtime/tests/test_verification_strength.py` — the finding itself; strength rising per
wired layer; a declared-but-unwired `review` scoring nothing; an unresolvable `validate.schema`
reported inert while a resolvable one counts; every agent reported but only roots as findings; the
pure function driven from a hand-built ledger; serialisation; and that each surface exists.

## Demo

`swarmkit validate` in `examples/sdlc-pipeline/workspace` reports 12 of 15 roots unchecked, and
`--require-verified` exits 1 — with `security-review/release-gate` shown as `judge, approve;
declared but inert: validate, review`, agreeing with what the reachability report independently
found on the same compile.
