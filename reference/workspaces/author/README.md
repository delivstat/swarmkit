# Dot Author workspace

A thin SwarmKit workspace whose only job is **wrapping an existing topology as a user-facing Dot**. Topology authoring itself lives in `swarmkit author` (per-mode system prompts + schema knowledge in `packages/runtime/src/swarmkit_runtime/authoring/`). This workspace does **not** replicate that.

The `author` topology is a conversational coworker that:

1. Asks the user what the new coworker should do (one line).
2. Asks which topology it should run on.
3. If the user does not know one exists, points them at:
   - `swarmkit topologies ls <workspace>` to list what's there, or
   - `swarmkit author topology <workspace>` to design a new one through conversation.
4. Once the user names an existing topology, calls `create-dot` to write the sidebar entry.

Consumers (the dots app, DOT, custom frontends) invoke this topology over AG-UI like any other Dot.

## Why the Dot Author doesn't author topologies

An earlier draft baked a full authoring charter into the Dot Author (output_schema rules, skill shapes, governance semantics, funnel wiring, a `search-swarmkit-docs` tool). That duplicated `swarmkit author`, got the schemas wrong without the right prompts, and gave the user two overlapping authoring surfaces to reason about.

The split instead:

- **SwarmKit authoring** (upstream, already shipped) — owns anything that is a SwarmKit artifact: topologies, skills, archetypes, MCP server configs. Carries the authoring charter, knows the schemas, writes the files.
- **Dot Author** (this workspace) — owns only what is unique to Dots: the sidebar entry (id, name, role, greeting, icon, renderers) + wiring to an existing topology.

A seamless v2 could extract `swarmkit author` as its own workspace topology and let the Dot Author invoke it via the `agent` skill backing so a user never leaves one chat. For now the Dot Author simply tells the user to run the CLI.

## Running

```bash
# From a repo checkout:
AUTHOR_TARGET_WORKSPACE=/path/to/your/workspace \
OPENROUTER_API_KEY=sk-or-... \
uv run swarmkit serve reference/workspaces/author

# Then invoke the `author` topology via AG-UI:
curl -X POST http://localhost:8000/api/ag-ui/run \
  -H 'content-type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "wrap morning-brief as a Dot called Morning Brief"}],
    "context": {"topology": "author"}
  }'
```

**Env variables**:
- `AUTHOR_TARGET_WORKSPACE` — where topologies live and where the Dot entry is checked against. Default: `$CWD/workspace`.
- `SWARMKIT_WORKSPACE` — path the command-pack `cwd` resolves against. Set to the mounted workspace path.
- `OPENROUTER_API_KEY` — the author uses Kimi K2 via OpenRouter; swap the model in `archetypes/dot-author.yaml` for a different provider.

## Shape

```
author/
├── workspace.yaml                       # declares the author-tools command_pack
├── archetypes/
│   └── dot-author.yaml                  # the narrow Dot-wrapping prompt
├── topologies/
│   └── author.yaml                      # single-agent conversational topology
├── skills/
│   └── create-dot.yaml                  # the only skill
└── command_packs/author-tools/
    └── create_dot.py                    # writes a Dot entry pointing at an existing topology
```

`create_dot.py` refuses to write a Dot entry pointing at a topology that is not already present — this is the guardrail that keeps the Dot Author from pretending to be a topology author. See its exit-code 3 (`topology_missing`).
