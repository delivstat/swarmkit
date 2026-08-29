---
title: Command packs
description: Local commands as a skill implementation type, declared in packs, governed by the same permission tiers as MCP servers.
tags: [skills, governance, schema]
status: draft
---

# Command packs

**Scope:** `packages/schema`, `packages/runtime` (skills, governance, workspace)
**Design reference:** §6 (skills), §8.5–§8.7 (governance), §18 (MCP integration)
**Status:** draft

## Goal

Let a skill invoke a **local command** instead of an MCP tool, under the permission model that
already governs MCP calls — and let commands be declared in **packs** so a topology grants a set
rather than a list.

## The premise this corrects

MCP was never the extension paradigm. Skills are, and `skill.schema.json` has always carried three
implementations: `mcp_tool`, `llm_prompt`, `composed`. Only one speaks MCP. This note adds a fourth,
`command`, and the workspace-level declaration it needs.

Invariant 2 in `CLAUDE.md` is untouched: skills remain the only capability extension primitive. A
command is a new way to *back* a skill, not a second way to extend the swarm.

## Why the permission model transfers unchanged

`check_mcp_permission` does four things, and none of them is MCP:

```python
unmet = prerequisites.missing(requires, ...)               # implementation-agnostic already
permission = mcp_manager.get_permission(server_id, tool_name)
if permission == "open" or governance is None: return True, ""
decision = await governance.evaluate_action(
    action=f"mcp:call:{server_id}:{tool_name}",
    scopes_required=scopes,
    context={"server_permission": permission})
```

And `get_permission` resolves `permission_overrides[tool] or server.permission` — a config lookup.
No protocol, no tool annotations, no round trip.

So what a command pack reuses is a **two-level (container, member) tier lookup plus a governance
action string**. Pack is the container, command the member. The tier machinery is not being mirrored;
it is being renamed to what it always was.

## API shape

### Workspace declaration

```yaml
command_packs:
  - id: json-tools
    permission: cautious                      # same four tiers as mcp_servers
    timeout: 30s                              # pack default; built-in default if omitted
    max_output: 10MB
    requires:
      - { binary: jq, version: '>=1.7' }      # checked at workspace load, not at run time
    commands:
      - id: query
        argv: [jq, '-r', '{filter}', '{file}']
        effects: read
        output: { parse: json }
      - id: edit-in-place
        argv: [jq, '-r', '--in-place', '{filter}', '{file}']
        effects: write
    permission_overrides:
      edit-in-place: strict
    timeout_overrides:
      big-report: 5m
```

### Skill implementation

```yaml
implementation:
  type: command
  pack: json-tools
  command: query
```

`inputs` on the skill supplies the typed parameter schema the model sees — the one thing MCP was
providing that a bare CLI does not. Substitution is **value-only into argv**; there is no shell, and
a substituted value is never re-parsed into arguments. This is the same rule the executor adapter DSL
already enforces (`launch.command` is argv, `$defs/template` is value-only), and it is the injection
boundary: a `{filter}` of `; rm -rf /` is an inert string.

### Governance

The action namespace is **generalised, not extended**:

```
tool:call:{provider}:{name}

  tool:call:mcp:filesystem:read_file
  tool:call:command:json-tools:query
```

`provider` is the *kind* — `mcp` or `command` — and `name` is the qualified
`{container}:{member}`. This is the shape that keeps the rule class we just broke expressible:

| rule | means |
| --- | --- |
| `tool:call:*` | every tool call, of any kind |
| `tool:call:mcp:*` | all MCP calls |
| `tool:call:mcp:filesystem:*` | one server |
| `tool:call:command:*` | all commands |

Making `provider` the container id instead would read more literally, but leaves no way to write
"all MCP calls" at all — and that is precisely the rule that stopped meaning what it said. It also
makes a pack id colliding with a server id harmless rather than a validator's problem.

`mcp:call:*` is **removed**, not deprecated. See the migration command below.

### Secrets and bounds

```yaml
command_packs:
  - id: github
    credentials_ref: gh-token
    env: { GH_TOKEN: '{credential.gh-token}' }   # secrets reach a command HERE
    commands:
      - id: list-prs
        argv: [gh, pr, list, '--repo', '{repo}'] # …and never here
```

**A credential may be substituted into `env`, never into `argv`** — `{credential.*}` appearing in an
argv template is a schema error. Three things follow from that one rule, and none of them has to be
remembered afterwards: a secret cannot be placed by a model, cannot land in an audit line that
records the command that ran, and cannot be read out of `ps` by anything else on the box.

The cost is real and accepted: CLIs that only take a credential as a flag need a wrapper. Buying the
guarantee back later would mean auditing every place a command line is logged, which is the kind of
retrofit that is never finished.

**Bounds mirror the tier shape** — `timeout` and `max_output` on the pack, `timeout_overrides` per
command. Ships with a conservative built-in default so an undeclared pack is still bounded; a command
with no ceiling is a command that can take a run down with it, and unbounded-by-default is not a
decision anyone makes deliberately.

## Four decisions this note is making

### 1. `effects` is declared per command, and defaults to `write`

For MCP, a permission tier is a hint laid over an opaque tool — the runtime cannot tell whether
`foo` writes. For a command, nothing is inferrable either: `curl` POSTs, `jq` and `sed` both take
`-i`. But the pack author *knows*, so they must say. An undeclared `effects` is `write`.

This makes `permission: readonly` genuinely enforceable for commands, which it never was for MCP.

### 2. Argv only — never a shell, and never a generic `bash` skill

A `command` skill fills declared slots in a frozen argv template. It does not compose invocations.

This is the line, and it is load-bearing. Governance names actions; `bash` is one action that means
anything, so no policy can be written over it. Structural gates (invariant 6) stop being structural
the moment an agent can `curl` a reserved endpoint, and the append-only audit log (§8.3) assumes
nothing executive can reach the file.

SwarmKit already admits arbitrary execution — the harness executor — and the containment it needed is
visible in `executors/`: `_sandbox.py`, `_egress.py`, `_approval.py`, `_budget.py`, `_container.py`.
A shell skill would need all of that again, for a capability that already exists one layer up. A
declarative argv skill needs none of it.

### 3. A pack grant is live for reads and frozen for writes

Granting a set rather than a list is the point of packs, and it creates a widening problem that is
the mirror image of the wildcard one: an agent granted `pack:json-tools` gains whatever is added to
that pack later, silently.

Split on the axis that matters, reusing the `effects` field decision 1 already requires:

```
pack gains { id: format, effects: read }
  → flows into every existing grant, logged

pack gains { id: publish, effects: write }
  → workspace load fails, naming each agent that holds
    the pack, until the grant is re-confirmed
```

A new read command interrupting nobody is worth the convenience; a new effectful command arriving
unannounced in five agents' capability sets is not.

### 4. Grants take packs *and* servers, not just skills

Agents currently grant skills one at a time. MCP has the identical problem today (declare a server,
write a skill per tool, grant each), and solving it only for commands makes the two paradigms diverge
on exactly the ergonomics this is meant to fix.

```yaml
skills: [pack:json-tools, server:filesystem, some-individual-skill]
```

Bigger change, better resting state, one mental model.

## Migration — `swarmkit policy check`

Generalising the namespace breaks every existing policy rule. That is the intended failure: the
alternative was `mcp:call:*` quietly covering less than it did the day before, and silent policy
weakening is the failure class `docs/notes/schema-change-discipline.md` exists for.

Breaking loudly is only acceptable with a one-shot repair, so the CLI ships with the change:

```
$ swarmkit policy check
✗ 7 rules use the removed `mcp:call:` namespace

  exact — unambiguous, rewritten as-is:
    workspace.yaml:41   mcp:call:filesystem:read_file
    workspace.yaml:44   mcp:call:filesystem:write_file
    iam/analyst.yaml:12 mcp:call:brain-search:query
    ... 3 more

  wildcard — literal meaning preserved, INTENT UNKNOWABLE:
    workspace.yaml:38   mcp:call:*  →  tool:call:mcp:*

$ swarmkit policy check --fix
✓ 7 rules rewritten across 3 files
⚠ 1 wildcard rule now covers MCP only. If it was meant to cover
  every tool call, change it to `tool:call:*`.
```

The rewrite is a pure prefix swap — `mcp:call:` → `tool:call:mcp:` — which is **mechanically exact
for a specific rule** and merely *literal* for a wildcard. No tool can know whether an author who
wrote `mcp:call:*` meant "all MCP calls" or "all tool calls", because until now those were the same
sentence. So `--fix` migrates the whole workspace in one pass, and reports the wildcards separately
rather than pretending they were unambiguous. Reporting them is the entire point: those are the rules
whose meaning the migration changes.

Scope is every artifact in the workspace that can carry an action string — `workspace.yaml`, IAM
policy files, archetypes and topologies — not just the file the user happened to think of.

## Bundled packs

Packs ship bundled, as the four harness adapters do. An earlier draft worried that a bundled pack is
an execution surface nobody chose — that was wrong: **availability is not access.** A bundled pack
still has to be declared in the workspace and granted to an agent, and the grant is the audit step.
Bundling only saves everyone writing the same `jq` pack by hand.

## Non-goals

- A generic shell or `bash` skill. See decision 2.
- Remote command execution. A pack runs on the machine running the swarm.
- Replacing MCP. Packs are for tools that already exist as binaries and would otherwise need a
  wrapper server written for them.
- Composition between commands. Two commands piped together is a `composed` skill, or it is a
  harness executor node — both already exist.

## Test plan

- **Unit** — tier resolution for `(pack, command)` mirrors the MCP table, including
  `permission_overrides`; `effects` defaulting to `write`; `readonly` denying a `write` command.
- **Unit** — argv substitution is value-only: a parameter containing `;`, `|`, `$(…)`, spaces and
  newlines reaches the process as exactly one argv entry. This is the security test and it should
  read like one.
- **Unit** — `requires.binary` missing at workspace load fails with the binary named; a version below
  the constraint fails the same way.
- **Integration** — a topology granting `pack:json-tools` resolves every command in the pack; a
  denied command returns the standard `DENIED_MARK` refusal, identical in shape to an MCP denial.
- **Integration** — a pack gaining a `read` command leaves existing grants valid; gaining a `write`
  command fails workspace load, naming every agent that holds the pack.
- **Migration** — `policy check --fix` over a fixture workspace with rules spread across
  `workspace.yaml`, an IAM file and an archetype rewrites all of them; specific rules map exactly,
  the wildcard is reported not silently reinterpreted; a second run is a no-op.
- **Test data** — a pack fixture under `packages/schema/tests/fixtures/`, plus an invalid one
  (shell metacharacters in `argv`, missing `requires`).

## Demo plan

`examples/command-packs/` — a workspace declaring a `json-tools` pack and a two-agent topology where
one agent holds `pack:json-tools` read commands and the other holds the `strict` write command.
Terminal transcript showing: a read command running unattended, a write command stopping at a
governance decision, and a `readonly` pack refusing the write with the reason printed.

The demo also runs `swarmkit policy check --fix` against a workspace carrying old-namespace rules,
since the migration is the part every existing user meets first.

The transcript must include the injection case — a filter parameter containing `; rm -rf /` running
harmlessly and producing an ordinary `jq` error — because that is the claim a reviewer will most want
to see rather than be told.

## Open questions

1. **The actual numbers** for the built-in timeout and output ceiling. The shape is decided; the
   values want measuring against real packs rather than picking a round number that looks sensible.
