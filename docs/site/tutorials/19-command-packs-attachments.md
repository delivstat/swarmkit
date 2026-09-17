# Level 19: Command packs and attachments

Two ways to put something real in front of an agent without writing a server: a **local binary as a
skill**, and a **file beside the run's input**.

## What you'll learn

- `command_packs`: a binary the workspace already has, as `implementation.type: command`
- Load-time checks: the binary, its version, and every skill's target — before any run
- Effects and tiers: why `pack:` grants carry reads only
- Attachments: `--attach` / `attachments:` — one model call, no tool round-trip
- What is refused and why: `type`, `url`, a bad path, a non-image

## The idea

MCP was never the extension paradigm; **skills** were, and a skill has several backings. When the
capability is `jq`, `git`, a linter or a script you already trust, wrapping it in an MCP server is a
server you now maintain. A command pack declares the binary and its commands in `workspace.yaml`; each
command becomes an ordinary skill at load, governed like an MCP tool ([Command packs](../design-notes/command-packs.md)).

Attachments are the other direction: the *caller* already holds the bytes — a snapshot, an upload, a
screenshot — and should not need an agent to go and fetch what it is carrying
([Getting an image to a model](../guides/getting-an-image-to-a-model.md)).

## Build it

### 1. Declare a pack

```yaml
# workspace.yaml
command_packs:
  - id: json-tools
    requires:
      - { binary: jq, version: ">=1.6" }     # checked at LOAD, naming the binary
    permission: readonly
    timeout_seconds: 10
    commands:
      - id: query
        argv: [jq, "-r", "{filter}", "{file}"]
        effects: read
      - id: rewrite
        argv: [jq, "-r", "{filter}", "{file}"]
        effects: write
```

Rules that hold at load, not on first use: a missing binary or a too-old version refuses the
workspace; a skill naming a pack or command that does not exist is a resolution error; a
`{credential.*}` placeholder in `argv` is refused outright (it would land in the audit line and in
`ps`) — secrets reach a command through the pack's `env` only.

### 2. Every command is already a skill

The runtime synthesizes `json-tools-query` and `json-tools-rewrite` at load. Grant them:

```yaml
agents:
  root:
    skills: [pack:json-tools]      # every READ command in the pack, now and later
    # or name one: [json-tools-query]
```

`pack:` carries **reads only** — adding a read command flows through to everyone holding the pack;
adding a write reaches nobody, so a pack can never silently widen an agent that already holds it. A
write command is named individually.

Write your own skill over a command when you want a better description or an input schema the
model learns from:

```yaml
implementation:
  type: command
  pack: json-tools
  command: query
iam:
  required_scopes: [workspace:read]
```

### 3. Attach a file to a run

```bash
swarmkit run ./workspace describe-scene --input "What is at the gate?" --attach snapshots/gate.jpg
```

```json
POST /run/describe-scene
{ "input": "What is at the gate?", "attachments": [{ "path": "snapshots/gate.jpg" }, { "data": "<base64>", "name": "wide.jpg" }] }
```

The file reaches the **entry agent's first message** and no downstream node. Its media type is read
from the bytes (there is no `type` field; sending one is a 422). Images only today, 20 MiB each.
`url` is refused — the runtime does not fetch caller-supplied addresses. A bad path is a 422 on the
request, so a job id means the file was readable. Every attachment is audited as `run.attachments`
by name, type, size and SHA-256 — never by content.

## Run it

```bash
just demo-command-packs      # every command it runs is python3 — nothing to install
```

```bash
SWARMKIT_PROVIDER=mock swarmkit run examples/hello-swarm/workspace hello \
  --input "Describe this" --attach docs/site/img/portal/connections.png
swarmkit logs examples/hello-swarm/workspace --last 1     # look for run.attachments
```

## What happened

- The pack's commands were checked and exposed at load; a `pack:` grant expanded to the read
  commands; each call went through the same permission seam as an MCP tool (`readonly` denies a
  `write` command, fail-closed).
- The attachment rode beside the input in one model call and left a checkable digest in the audit.

## Learn more

- [Command packs](../design-notes/command-packs.md) · [Workspace artifact](../reference/workspace.md) (`command_packs`)
- [Skills reference](../reference/skills.md) — the `command` backing
- [Serve mode](../reference/serve.md) — the `attachments` field table
- [Images on both executors](../design-notes/images-on-both-executors.md) — why attachments and the `view-file` skill are one media path
