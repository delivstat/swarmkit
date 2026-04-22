# hello-swarm

SwarmKit's on-ramp example — the smallest workspace that still exercises
every piece of the mental model: a topology that references an archetype
that references a skill.

Milestone 1 closes with this example. `swarmkit validate` against
`workspace/` resolves cleanly; against `workspace-broken/` it prints the
kind of error a first-time user will actually hit.

## From a fresh clone

Prerequisites: Python 3.11+, [`uv`](https://astral.sh/uv), and optionally
[`just`](https://github.com/casey/just). You do **not** need `pnpm` —
this example is pure Python.

```bash
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
uv sync --all-packages            # installs runtime + schema
just demo-resolver                # runs both workspaces below
```

Without `just`, run the CLI directly:

```bash
uv run swarmkit validate examples/hello-swarm/workspace --tree
uv run swarmkit validate examples/hello-swarm/workspace-broken
```

The first exits `0` and prints the resolved agent tree. The second exits
`1` and prints an `agent.unknown-archetype` error with a file pointer and
a `try:` line.

**Today "run" means validate + resolve + inspect** — the execution engine
lands in Milestone 2+ (see `design/IMPLEMENTATION-PLAN.md`). Edit any
file under `workspace/` and re-run the command to see the error it
produces.

## The pieces

```
workspace/
├── workspace.yaml              # id + metadata for the workspace
├── topologies/hello.yaml       # root supervisor + one worker child
├── archetypes/greeter.yaml     # worker defaults: model, prompt, skills
└── skills/say-hello.yaml       # capability skill (MCP tool reference)
```

**`workspace.yaml`** — just identity. The resolver finds everything else
by walking the directory structure.

**`topologies/hello.yaml`** — the graph. Two agents: a root supervisor
with model/prompt declared inline, and a child worker that inherits
everything from the `greeter` archetype.

**`archetypes/greeter.yaml`** — reusable defaults. Any agent that says
`archetype: greeter` picks up this model, prompt, and skill list. Agents
can override individual fields — see
`packages/runtime/tests/fixtures/workspaces/resolved-tree/topologies/review.yaml`
for inheritance + override patterns.

**`skills/say-hello.yaml`** — a capability skill pointing at an MCP tool.
Skills are the only extension primitive; category (`capability`,
`decision`, `coordination`, `persistence`) tells the runtime how they're
invoked.

Execution isn't wired up yet — that's Milestone 2+. For now, `swarmkit
validate` proves the artefacts load, resolve, and type-check as a
coherent whole.

## The broken variant

`workspace-broken/` is byte-identical to `workspace/` except for one
character in `topologies/hello.yaml`:

```yaml
archetype: greter   # typo — the real id is 'greeter'
```

Validate it to see the error. The point is that the output alone —
without opening the design doc or a knowledge base — tells a reader:

- which file and which JSON pointer
- which rule failed (`agent.unknown-archetype`)
- what to do next (define the archetype, or drop the reference)

That's the user experience Milestone 1 commits to.

## Where to go next

- Real schema details: `design/details/topology-schema-v1.md`,
  `archetype-schema-v1.md`, `skill-schema-v1.md`.
- The full resolver pipeline: `design/details/topology-loader.md`.
- Error catalogue: the `rule` line on every validation error is
  grep-friendly against the same document.
