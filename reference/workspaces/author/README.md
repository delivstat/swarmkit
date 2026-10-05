# Author Dot workspace

A reusable SwarmKit authoring surface. The `author` topology is a conversational coworker whose job is **creating other coworkers (Dots)**. It elicits the requirement in plain language, looks up anything it needs from `llms-full.txt`, generates a complete topology YAML, and calls the `create-dot` skill to write both the topology file and the Dot entry.

Consumers invoke this topology over AG-UI like any other Dot. The dots app wires its `/dots/author` route to it; any other app (DOT, control-plane UI, custom frontends) can do the same.

## The authoring charter

Baked into the `dot-author` archetype's system prompt. Every coworker the Author creates must satisfy:

1. **Every topology ships with `output_schema`.** No exceptions. Shape it to match the renderer the coworker will use. The schema goes in a sibling `schemas/<id>.schema.json` file; the agent's `output_schema` field is a path, not an inline object.
2. **Use skills for I/O.** Never prompt around a missing tool. If the skill doesn't exist in the current workspace, say so.
3. **`requires_credentials` on skills that need them.** The Connections UI depends on it.
4. **Funnels are for human approval**, not for shape validation. Reach for one only when a human sign-off is genuinely needed.
5. **Prefer existing archetypes.** Declare a new one only when the role genuinely differs.
6. **Governance decision skills for any judgement step.**
7. **`search-swarmkit-docs` before guessing.** The llms-full doc is authoritative; LLM training data is not.
8. **One `create-dot` tool call per creation.** Don't describe what you would create — create it.

Edit `archetypes/dot-author.yaml` to tune the rules.

## Running

```bash
# From a repo checkout:
AUTHOR_TARGET_WORKSPACE=/path/to/your/workspace \
AUTHOR_LLMS_FULL_PATH=/path/to/swarmkit/llms-full.txt \
OPENROUTER_API_KEY=sk-or-... \
uv run swarmkit serve reference/workspaces/author

# Then invoke the `author` topology via AG-UI:
curl -X POST http://localhost:8000/api/ag-ui/run \
  -H 'content-type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "I need a coworker that triages my inbox"}],
    "context": {"topology": "author"}
  }'
```

**Env variables**:
- `AUTHOR_TARGET_WORKSPACE` — where generated topologies land. Default: `$CWD/workspace`.
- `AUTHOR_LLMS_FULL_PATH` — path to `llms-full.txt`. Default: `./llms-full.txt`, falling back to `/app/llms-full.txt`.
- `OPENROUTER_API_KEY` — the author uses Kimi K2 via OpenRouter; swap the model in `archetypes/dot-author.yaml` for a different provider.

## Shape

```
author/
├── workspace.yaml                        # declares command_packs
├── archetypes/
│   └── dot-author.yaml                   # the authoring charter + skills
├── topologies/
│   └── author.yaml                       # single-agent conversational topology
├── skills/
│   ├── create-dot.yaml                   # writes topology YAML + Dot entry
│   └── search-swarmkit-docs.yaml         # keyword search over llms-full.txt
└── command_packs/author-tools/
    ├── create_dot.py                     # validates the YAML before writing
    └── search_docs.py                    # heading-aware chunking + token scoring
```

## Design decisions

- **Chat agent, no `output_schema`.** The topology opts out (`output_schema: null`) because correctness lives in the `create-dot` tool call, not in the chat prose. This is the one legitimate case for the opt-out — don't copy the pattern for working coworkers.
- **No funnel.** No human-approval gate on creating a Dot in a user's own workspace. If an installation wants an approval step, add one.
- **Target-workspace at runtime, not install-time.** `AUTHOR_TARGET_WORKSPACE` lets one Author serve many consumer apps.
- **Keyword search for v1.** `search_docs.py` is a 70-line heading-aware substring scorer. Swap in a retrieval backend later without changing the skill contract.

## What's not here

- A UI wiring. The dots app's `/dots/author` route is updated to invoke this topology in a follow-up PR.
- Topology editing. The Author creates; it does not modify existing Dots.
- Schema-file writing. `create-dot` writes the topology YAML; if that YAML's `output_schema` is a path, the Author must also create the schema file. Future work: a `create-schema-file` sibling tool.
