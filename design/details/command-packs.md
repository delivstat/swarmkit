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

```
command:call:{pack}:{command}
```

## Three decisions this note is making

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

### 3. Grants take packs *and* servers, not just skills

Agents currently grant skills one at a time. Granting a whole pack is the ergonomic point of packs —
but MCP has the identical problem today (declare a server, write a skill per tool, grant each), and
solving it only for commands makes the two paradigms diverge on exactly the ergonomics this is meant
to fix.

```yaml
skills: [pack:json-tools, server:filesystem, some-individual-skill]
```

Bigger change, better resting state, and it keeps one mental model.

## The risk worth being loud about

**Adding this namespace weakens existing policies without editing them.** A rule written as
`mcp:call:*` means "all tool calls" today and "all MCP tool calls" the moment command packs ship.
Nobody touches the rule; it just covers less. Silent policy weakening is the failure class
`docs/notes/schema-change-discipline.md` exists for.

Mitigation, and it is not optional: a **workspace-load lint** that errors when a policy contains an
`mcp:call:` wildcard and the workspace declares `command_packs` — naming both, and requiring the
author to say what they meant.

The alternative is to generalise the action to `tool:call:{provider}:{name}`, which breaks every
existing policy on day one. That is the louder failure and arguably the right one; it is left open
below.

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
- **Integration** — the wildcard lint fires on a workspace with an `mcp:call:*` policy rule and a
  declared pack.
- **Test data** — a pack fixture under `packages/schema/tests/fixtures/`, plus an invalid one
  (shell metacharacters in `argv`, missing `requires`).

## Demo plan

`examples/command-packs/` — a workspace declaring a `json-tools` pack and a two-agent topology where
one agent holds `pack:json-tools` read commands and the other holds the `strict` write command.
Terminal transcript showing: a read command running unattended, a write command stopping at a
governance decision, and a `readonly` pack refusing the write with the reason printed.

The transcript must include the injection case — a filter parameter containing `; rm -rf /` running
harmlessly and producing an ordinary `jq` error — because that is the claim a reviewer will most want
to see rather than be told.

## Open questions

1. **Namespace.** `command:call:*` alongside `mcp:call:*`, or generalise both to
   `tool:call:{provider}:{name}`? The first is compatible and silently weakens existing rules; the
   second breaks them visibly. Leaning toward the second, with the lint as the migration aid.
2. **Do packs ship bundled?** The executor adapters ship four harnesses as YAML. The parallel would
   be a small bundled library — `json-tools`, `text-tools` — which is convenient and is also how a
   default surface becomes an unaudited one.
3. **Timeouts and output limits.** A command that hangs or emits a gigabyte needs a bound. Probably
   per-pack with a per-command override, mirroring the tier shape, but the numbers need measuring
   rather than guessing.
4. **Working directory and environment.** `mcp_server` has `cwd` and `env` with `${VAR}` expansion.
   Packs presumably want the same, which also means they inherit the same question about what a
   command can see.
