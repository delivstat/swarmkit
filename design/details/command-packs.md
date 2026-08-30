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

So what a command pack reuses is a **two-level (container, member) tier lookup**, plus the scopes the
skill already declares. Pack is the container, command the member. The tier machinery is not being
mirrored; it is being renamed to what it always was.

Note the two inputs to `evaluate_action` do different jobs, and the next section separates them:
`scopes_required` is what authorizes, `action` is what labels.

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

### Governance — scopes authorize, actions label

These are two different things and the distinction is easy to lose.

**Scopes are the gate.** OIDC-style `noun:verb`, declared per skill in `iam.required_scopes`, and
the only authorization test the runtime performs:

```python
granted = scopes_required & allowed_scopes
denied  = scopes_required - allowed_scopes
if denied: → deny
```

Real values in `reference/skills/`: `workspace:read`, `workspace:write`, `knowledge:read`,
`repo:read`, `tests:execute`. **A command skill declares these exactly like any other skill.** That
is the whole governance integration — the pack contributes a permission tier, the skill contributes
scopes, and nothing new is needed.

**The action string is a label, not a rule.** `mcp:call:{server}:{tool}` is passed alongside the
scopes and is used for three things: a substring scan under the `readonly` tier, the human-readable
`reason`, and the audit event. Nothing pattern-matches it — a search of `reference/`, `examples/`,
`docs/` and `packages/` finds no policy artifact written against it, only prose describing the
convention and two tests asserting the emitted string.

So commands take a sibling namespace and `mcp:call:` is left alone:

```
mcp:call:{server}:{tool}          unchanged
command:call:{pack}:{command}     new
```

An earlier draft of this note proposed generalising both to `tool:call:{provider}:{name}`, on the
theory that a rule written `mcp:call:*` would silently stop covering everything. **That theory was
wrong** — no such rules exist, because actions are not matchable. The rename would have bought
consistency at the cost of audit-history continuity, which is a bad trade for a string nothing reads.

### Structure goes in the payload, not the string

What the generalisation was actually reaching for is better served by structure. `AuditEvent.payload`
is already `dict[str, object]`, so this needs no schema change:

```yaml
action:  command:call:json-tools:query
payload:
  provider:  command        # mcp | command
  container: json-tools     # server or pack id
  member:    query
  effects:   read
```

If action-matching policies ever arrive, they match fields rather than parsing a colon-joined string,
and every question about how to segment that string dissolves. The string stays a display format.

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

This makes `permission: readonly` genuinely enforceable, and it replaces a heuristic rather than
adding a field. Today the `readonly` tier decides write-ness by substring-scanning the action string:

```python
_write_signals = ("create","delete","update","write","modify","edit","insert","drop","push","send")
if any(sig in action.lower() for sig in _write_signals): → deny
```

So `truncate_table` and `purge_cache` pass `readonly` today — neither substring appears — while
`send_query` is denied though it only reads. Commands pass `effects` through the decision `context`
and are never sniffed. The MCP side has the same bug and is worth fixing independently of this note.

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

### 3. A pack grant carries the pack's read commands only

Granting a set rather than a list is the point of packs, and it creates a widening problem that is
the mirror image of the wildcard one: an agent granted `pack:json-tools` would gain whatever is
added to that pack later, silently.

```yaml
skills:
  - pack:json-tools         # every READ command, now and later
  - json-editing-rewrite    # a write, named — bulk grants never carry one
```

A read command added to the pack flows through to everyone holding it, which is the ergonomics the
bulk form exists for. A write command never does.

> **Amended during implementation.** This note originally said a pack gaining a write command should
> **fail workspace load** until every holding agent re-confirmed. That needs a stored list of
> acknowledged commands living somewhere, which is state in what is meant to be a declarative
> artifact — and topology-as-data is the first invariant in `CLAUDE.md`. Excluding writes from the
> bulk form gets the same safety property with no stored state and no load-time failure: the grant
> means exactly what it says on the line, every time it is read. The rejected version is recorded
> because "it fails loudly" sounded like the safer answer and was the more complicated one.

`server:<id>` deliberately makes no equivalent promise. An MCP tool has no declared effect to filter
on — that is [#825](https://github.com/delivstat/swarmkit/issues/825) — so a server grant carries
everything targeting that server. The asymmetry is honest; pretending otherwise would be worse.

### 4. Grants take packs *and* servers, not just skills

Agents currently grant skills one at a time. MCP has the identical problem today (declare a server,
write a skill per tool, grant each), and solving it only for commands makes the two paradigms diverge
on exactly the ergonomics this is meant to fix.

```yaml
skills: [pack:json-tools, server:filesystem, some-individual-skill]
```

Bigger change, better resting state, one mental model.

**Implementation note.** A pack command becomes an ordinary skill at registry-build time, with the
id `<pack>-<command>`. That is what keeps the tool builder, `requires:` validation, the archetype
merge and the UI from each needing to know packs exist — a command *is* a skill from the moment the
registry is built. A synthetic id colliding with a hand-authored skill is a resolution error naming
both, rather than one shadowing the other.

A bulk grant matching nothing is also an error, not an empty set: an agent silently granted no tools
is indistinguishable from one whose model chose not to use them, and that only surfaces later as a
puzzling transcript.

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
- **Unit** — the audit payload carries `provider`, `container`, `member` and `effects` as fields, for
  both an MCP call and a command call, so a query never parses the action string.
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

1. **The actual numbers** for the built-in timeout and output ceiling. The shape is decided; the
   values want measuring against real packs rather than picking a round number that looks sensible.
