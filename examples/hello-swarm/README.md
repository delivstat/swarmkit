# hello-swarm

Swael's on-ramp example — the smallest workspace that still exercises
every piece of the mental model: a topology that references an archetype
that references a skill.

Milestone 1 closes with this example. `swael validate` against
`workspace/` resolves cleanly; against `workspace-broken/` it prints the
kind of error a first-time user will actually hit.

## From a fresh clone

Prerequisites: Python 3.11+, [`uv`](https://astral.sh/uv), and optionally
[`just`](https://github.com/casey/just). You do **not** need `pnpm` —
this example is pure Python.

```bash
git clone git@github.com:delivstat/swael.git && cd swael
uv sync --all-packages            # installs runtime + schema
just demo-resolver                # runs both workspaces below
```

Without `just`, run the CLI directly:

```bash
uv run swael validate examples/hello-swarm/workspace --tree
uv run swael validate examples/hello-swarm/workspace-broken
```

The first exits `0` and prints the resolved agent tree. The second exits
`1` and prints an `agent.unknown-archetype` error with a file pointer and
a `try:` line.

Once `validate` is happy, run the topology end-to-end. Pick a provider
via env vars (or rely on the agent's declared provider):

```bash
SWARMKIT_PROVIDER=google SWARMKIT_MODEL=gemini-2.5-flash \
  uv run swael run examples/hello-swarm/workspace hello \
  --input "Greet the engineering team"
```

The runtime launches `hello_world_server.py` as a stdio MCP subprocess,
the supervisor delegates to the greeter worker, and the greeter calls
the `say-hello` skill. The skill resolves to the `hello-world.greet`
MCP tool — the literal greeting the server returns is the topology's
output. Other env-var combinations work too: `SWARMKIT_PROVIDER=anthropic`
with a `claude-*` model, or `SWARMKIT_PROVIDER=openrouter` against an
OpenRouter-hosted model.

`just demo-run` runs the same command without the env-var preamble — set
your preferred provider env once and the demo just works.

## The pieces

```
workspace/
├── workspace.yaml              # id + mcp_servers (declares hello-world)
├── hello_world_server.py       # FastMCP stdio server, one tool: greet
├── topologies/hello.yaml       # root supervisor + one worker child
├── archetypes/greeter.yaml     # worker defaults: model, prompt, skills
└── skills/say-hello.yaml       # capability skill → hello-world.greet
```

**`workspace.yaml`** — identity plus the workspace's MCP server registry.
Skills reference servers by id; the runtime launches stdio servers as
subprocesses with `cwd` set to this directory, so the path
`hello_world_server.py` resolves naturally. The resolver still finds
topologies / archetypes / skills by walking the directory structure
(`.yaml` only — the `.py` server file is ignored by discovery).

**`topologies/hello.yaml`** — the graph. Two agents: a root supervisor
with model/prompt declared inline, and a child worker that inherits
everything from the `greeter` archetype.

**`archetypes/greeter.yaml`** — reusable defaults. Any agent that says
`archetype: greeter` picks up this model, prompt, and skill list. Agents
can override individual fields — see
`packages/runtime/tests/fixtures/workspaces/resolved-tree/topologies/review.yaml`
for inheritance + override patterns.

**`skills/say-hello.yaml`** — a capability skill pointing at the
`hello-world` MCP server's `greet` tool. Skills are the only extension
primitive; category (`capability`, `decision`, `coordination`,
`persistence`) tells the runtime how they're invoked.

**`hello_world_server.py`** — the smallest reasonable MCP server, using
`mcp.server.fastmcp.FastMCP`. One tool, `greet(audience)`, that returns
a static greeting. Real servers expose dozens of tools and back them by
APIs, databases, or filesystems — this file just proves the wire works
without distraction.

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
