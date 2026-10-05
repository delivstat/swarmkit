---
title: Authoring is a bundled workspace surfaced via `swarmkit:author:*`
description: `swarmkit author` ships as a workspace inside swarmkit-runtime and is reached from any serving/running workspace through a reserved namespace. Replaces the hardcoded Python authoring module.
tags: [cli, authoring, serve, iam]
status: proposed
tracking: "#1045"
---

## Why this exists

Authoring today lives in `packages/runtime/src/swarmkit_runtime/authoring/` — three Python
modules (`_prompts.py`, `_agent.py`, `_tools.py`, ~1,370 lines) that implement a custom
model-call loop, a hand-rolled tool-use dispatcher, and inline system prompts. It is the
only surface in SwarmKit that bypasses what every other run goes through: no job row, no
`/usage` aggregation, no audit entry, no governance seam, no composed/decision skills
reusable against the author, no operator-editable prompts.

This note describes what authoring **should be** — a workspace that ships inside
`swarmkit-runtime` and is surfaced from the serving/running workspace through a reserved
`swarmkit:author:*` namespace. Not a next phase; this is how `swarmkit author` works. The
Python module is a stopgap on the way to it.

## Shape

### Layout

```
packages/runtime/src/swarmkit_runtime/
├── authoring_workspace/                  # ships as package data
│   ├── workspace.yaml
│   ├── archetypes/
│   │   └── topology-author.yaml          # shared archetype, mode-specific prompts live on topologies
│   ├── topologies/
│   │   ├── topology.yaml                 # one per mode
│   │   ├── skill.yaml
│   │   ├── archetype.yaml
│   │   ├── mcp-server.yaml
│   │   └── init.yaml
│   ├── skills/
│   │   ├── read-workspace.yaml
│   │   ├── write-file.yaml
│   │   ├── validate-workspace.yaml
│   │   └── search-skills-catalogue.yaml
│   └── command_packs/author-tools/*.py
└── authoring/
    └── _resolver.py                       # get_authoring_workspace_path(), is_authoring_id()
```

Everything the current `_prompts.py` carries lands as `defaults.prompt.system` on each
topology's root archetype — operator-editable, versionable, reviewable.

### Namespace: `swarmkit:author:*`

A single reserved topology id prefix. The topology / skill / archetype id validator
rejects any user-defined id starting with `swarmkit:` with a clear error
(`topology.reserved-namespace`). Existing user workspaces are unaffected — ids with colons
weren't valid under the current pattern.

CLI and `swarmkit serve` both resolve `swarmkit:author:<mode>` to the bundled workspace's
`topologies/<mode>.yaml`. The user's workspace needs no changes.

### Reachability from a user workspace

The key design decision: authoring must be reachable **from inside another workspace**,
because CLI and `serve` are both tied to one user workspace.

#### CLI

```bash
swarmkit author topology /path/to/user-ws
# shim → swarmkit run /path/to/user-ws swarmkit:author:topology --input "<requirement>"
```

The CLI resolves `swarmkit:author:topology` against the bundled workspace, but the **run
executes in the user's workspace context** — their providers, credentials, governance,
env. The author writes into `/path/to/user-ws`.

#### `swarmkit serve`

```
GET /topologies →
  [
    "my-user-topology",
    "swarmkit:author:topology",      # bundled, namespaced, opt-in
    "swarmkit:author:skill",
    "swarmkit:author:archetype",
    "swarmkit:author:mcp-server",
    "swarmkit:author:init",
  ]
```

Opt-in in `workspace.yaml`:

```yaml
authoring:
  expose: true
  iam:
    write_file_scope: workspace
```

Default off server-side (don't surprise an operator by exposing a write primitive to any
AG-UI client), on for the CLI (the operator explicitly ran `swarmkit author`).

A client (dots app, portal, custom frontend) posts `/api/ag-ui/run` with
`context.topology: "swarmkit:author:topology"` and gets the real authoring agent
streaming through CopilotKit the normal way — no subprocess, no stdin-pipe.

#### Dot Author (dots app)

```yaml
# reference/workspaces/author/skills/author-topology.yaml
implementation:
  type: agent
  topology: swarmkit:author:topology
```

The Dot Author becomes a trivial delegate once the namespace is wired.

### Two-workspace composition

When a `swarmkit:author:*` topology is invoked, two workspaces compose at run time:

| part | source |
|---|---|
| topology YAML, archetype, author-local skills, author command_packs | **bundled** authoring workspace |
| model_providers, credentials, governance config, env | **serving** / CLI-target workspace |
| target for the `write-file` skill | **serving** / CLI-target workspace (IAM-scoped) |

So the user's provider setup (OpenRouter, Claude Code harness, whatever) powers the
author. The author's output lands in the user's workspace. The user's governance decides
whether a Funnel gates the write.

The `target_workspace` is **implicit** — it is whichever workspace the author is invoked
from. No extra context plumbing beyond threading the serving workspace path through the
run request.

### IAM safety

The `write-file` skill is the one dangerous primitive. It is scoped to
`<target_workspace>/{topologies,skills,archetypes,funnels,workspace.yaml}` only — nowhere
else on disk. The scope is enforced inside the skill, not in the system prompt. The
current `_tools.py` is unscoped; this is a strict improvement.

The `authoring.iam.write_file_scope` key in `workspace.yaml` controls scope. Values:

- `workspace` (default) — scoped to the serving workspace path.
- `off` — the skill is unavailable (authoring can plan but cannot write; useful for
  dry-run or when the user wants to review generated YAML before anything lands).

### Discovery over `/topologies`

Listed with namespace prefix. Clients that don't know about namespaces see them as
regular topology names and treat them identically.

## Rollout

**Multi-PR**, not a flag day. One PR per slice, each independently reviewable:

1. **Foundation** (this note's first PR): design note, bundled workspace scaffold, YAML
   ports of the five mode prompts, `_resolver.py`, reserved-prefix guard, unit tests.
   No functional change — nothing invokes the bundled workspace yet.
2. **CLI shim**: `swarmkit author <mode> <ws>` resolves to the bundled topology and runs
   it in the user's workspace. Legacy path kept behind a `--legacy` flag for one
   release.
3. **Serve exposure**: `GET /topologies` surfaces `swarmkit:author:*` when
   `authoring.expose: true`. AG-UI run accepts namespaced ids.
4. **IAM-scoped write-file**: path check inside the skill; `authoring.iam.write_file_scope`.
5. **Dot Author swap**: `agent → swarmkit:author:topology` in `reference/workspaces/author/`.
6. **Bench harness switch**: `swarmkit run` instead of stdin-pipe.
7. **Delete legacy**: `authoring/_prompts.py`, `_agent.py`, `_tools.py` removed. `_resolver.py` stays.

## Non-goals

- Rewriting the authoring prompts themselves. The YAML forms carry the tightened language
  from #1044 and `fix/author-skill-mcp-catalogue` verbatim.
- Changing the CLI UX. `swarmkit author topology <ws>` keeps working.
- Any dots-app behavioural change. The Author Dot in #1040 just gets a cleaner
  delegation path when the namespace is wired.

## Done when

- `packages/runtime/src/swarmkit_runtime/authoring_workspace/` ships, validates with
  `swarmkit validate`, is included in the wheel's `package_data`.
- `swarmkit author topology <ws>` behaves identically from the user's POV but creates a
  real job row visible in `/jobs/history`.
- `swarmkit serve <ws>` with `authoring.expose: true` lists the `swarmkit:author:*`
  topologies and accepts AG-UI invocations.
- Bench harness switches from the stdin-pipe runner to the normal `swarmkit run` CLI
  with no quality regression on the #1044 / `fix/author-skill-mcp-catalogue` prompt rules.
- `reference/workspaces/author/` swaps its skill to `agent → swarmkit:author:topology`;
  subprocess shelling is gone.
- `packages/runtime/src/swarmkit_runtime/authoring/_prompts.py`, `_agent.py`, `_tools.py`
  are deleted. One authoring path, not two.
