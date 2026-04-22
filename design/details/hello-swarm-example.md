# hello-swarm example — M1 exit demo

## Goal

Close Milestone 1 by shipping the runnable artefact the plan calls for:

> **Exit demo:** `swarmkit validate examples/hello-swarm/workspace/` prints a
> resolved tree with all archetype/skill refs expanded. A first-time user
> can understand a deliberate validation failure from the error message
> alone — no design-doc lookup required.
>
> — `design/IMPLEMENTATION-PLAN.md`, Milestone 1

The example is the on-ramp for a brand-new user. It is **not** a feature
showcase: it demonstrates the mental model (workspace → topologies +
archetypes + skills) with the smallest possible artefact set.

## Non-goals

- Executing the swarm. Runtime execution lands in M2+; M1 only resolves.
- Demonstrating every schema field. The reference topologies under
  `reference/` (authored post-M1) are the canonical exhaustive examples.
- Covering every `ResolutionError` code. The fixtures under
  `packages/runtime/tests/fixtures/workspaces-invalid/` already do that.
  The broken variant here only needs to prove the error UX is readable.

## Shape

```
examples/hello-swarm/
├── README.md
├── workspace/                 # valid — used by `swarmkit validate --tree`
│   ├── workspace.yaml
│   ├── topologies/hello.yaml
│   ├── archetypes/greeter.yaml
│   └── skills/say-hello.yaml
└── workspace-broken/          # same tree with one deliberate typo
    ├── workspace.yaml
    ├── topologies/hello.yaml  # references archetype 'greter' (typo)
    ├── archetypes/greeter.yaml
    └── skills/say-hello.yaml
```

**Why two workspaces in one example.** The plan's exit demo has two
halves: "prints a resolved tree" and "understand a deliberate validation
failure". Folding both into one example means `just demo-resolver` shows
the reader both the success and failure UX without asking them to edit
files. `workspace-broken/` differs from `workspace/` by exactly one
character so the cause of the error is obvious from a diff.

**Why one skill, not three.** New users need to see every piece fit
together once, not every piece repeated. One `capability` skill is enough
to show the skill-reference path; archetype defaults and inheritance are
already demonstrated by the supervisor/worker split.

## Demo

`just demo-resolver` runs:

1. `swarmkit validate examples/hello-swarm/workspace --tree` — exit 0,
   prints the resolved agent tree (archetype defaults expanded, skills
   listed by id).
2. `swarmkit validate examples/hello-swarm/workspace-broken` — exit 1,
   prints an `agent.unknown-archetype` error block. The `try:` suggestion
   is enough for the reader to connect `'greter'` to the actual archetype
   id `'greeter'`.

The just target is marked so the second command's non-zero exit is
expected and doesn't fail the target.

## Test plan

`packages/runtime/tests/test_hello_swarm_example.py`:

- `test_hello_swarm_valid_workspace_resolves` — loads
  `examples/hello-swarm/workspace`, asserts no errors, asserts the tree
  contains the expected agent ids and the archetype defaults were
  inherited.
- `test_hello_swarm_broken_workspace_surfaces_unknown_archetype` — loads
  `examples/hello-swarm/workspace-broken`, asserts exactly one
  `ResolutionError` with code `agent.unknown-archetype`.

These tests are light but matter: without them, any refactor to the
resolver could silently break the published on-ramp.

## Deferred (follow-up, not this PR)

- **`test(runtime): resolve every reference/ artifact`** (plan item).
  Currently blocked — `reference/` contains only `.gitkeep` files; the
  v1.0 reference topologies have not been authored yet. The test lands
  with the reference topologies, not here. Tracked as its own task.
