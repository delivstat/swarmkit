# hello-swarm example — M1 exit demo

## Goal

Close Milestone 1 by shipping the runnable artefact the plan calls for:

> **Exit demo:** `swael validate examples/hello-swarm/workspace/` prints a
> resolved tree with all archetype/skill refs expanded. A first-time user
> can understand a deliberate validation failure from the error message
> alone — no design-doc lookup required.
>
> — `design/IMPLEMENTATION-PLAN.md`, Milestone 1

The example is the on-ramp for a brand-new user. It is **not** a feature
showcase: it demonstrates the mental model (workspace → topologies +
archetypes + skills) with the smallest possible artefact set.

## Non-goals

- Demonstrating every schema field. The reference topologies under
  `reference/` (authored post-M1) are the canonical exhaustive examples.
- Covering every `ResolutionError` code. The fixtures under
  `packages/runtime/tests/fixtures/workspaces-invalid/` already do that.
  The broken variant here only needs to prove the error UX is readable.

## Shape

```
examples/hello-swarm/
├── README.md
├── workspace/                 # valid — used by `swael validate --tree`
│   ├── workspace.yaml         # declares the hello-world MCP server
│   ├── hello_world_server.py  # tiny FastMCP server, stdio, one tool
│   ├── topologies/hello.yaml
│   ├── archetypes/greeter.yaml
│   └── skills/say-hello.yaml  # capability skill targeting hello-world
└── workspace-broken/          # same tree with one deliberate typo
    ├── workspace.yaml
    ├── topologies/hello.yaml  # references archetype 'greter' (typo)
    ├── archetypes/greeter.yaml
    └── skills/say-hello.yaml
```

The `say-hello` skill has `implementation.type: mcp_tool` and points at
`server: hello-world`. The server is a single Python file using
``mcp.server.fastmcp.FastMCP`` that exposes one tool, ``greet(audience)``.
The runtime launches it as a stdio subprocess via the workspace's
``mcp_servers`` block, with cwd set to the workspace root so the relative
script path resolves predictably.

Putting the server inside the workspace folder is intentional: it stays
out of the artefact discovery glob (``.yaml``/``.yml`` only) and reads
naturally to a first-time user as "everything this swarm needs lives
here".

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

Two demos cover the two halves of the on-ramp.

`just demo-resolver` runs the validation UX:

1. `swael validate examples/hello-swarm/workspace --tree` — exit 0,
   prints the resolved agent tree (archetype defaults expanded, skills
   listed by id).
2. `swael validate examples/hello-swarm/workspace-broken` — exit 1,
   prints an `agent.unknown-archetype` error block. The `try:` suggestion
   is enough for the reader to connect `'greter'` to the actual archetype
   id `'greeter'`.

The second command's non-zero exit is expected and the just recipe
silences it.

`just demo-run` runs the topology end-to-end with whichever model
provider is available in the environment (``SWARMKIT_PROVIDER`` /
``SWARMKIT_MODEL`` override, otherwise the agent-declared provider).
The runtime launches ``hello_world_server.py`` as a stdio subprocess,
the supervisor delegates to the greeter, the greeter calls the
``hello-world.greet`` MCP tool, and the literal greeting flows back
through the topology to stdout.

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
