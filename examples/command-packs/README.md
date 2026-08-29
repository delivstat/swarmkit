# Command packs

Local commands as a skill implementation type — the sibling of `mcp_servers` for capabilities that
already exist as binaries and would otherwise need a wrapper server written for them.

Design: [`design/details/command-packs.md`](../../design/details/command-packs.md).

## Run the demo

```bash
uv run python examples/command-packs/demo.py     # or: just demo-command-packs
```

Needs nothing installed — every command it runs is `python3`.

## What a pack looks like

```yaml
command_packs:
  - id: json-tools
    requires:
      - { binary: jq, version: '>=1.6' }
    permission: readonly
    timeout_seconds: 10
    commands:
      - id: query
        argv: [jq, '-r', '{filter}', '{file}']
        effects: read
```

and a skill that uses it:

```yaml
implementation:
  type: command
  pack: json-tools
  command: query
iam:
  required_scopes: [workspace:read]
```

## The five things worth understanding

**`argv`, never a shell.** A `{placeholder}` is filled with the *value* of an argument and stays
exactly one argv entry. `; rm -rf /` is a jq filter that fails to parse, not a command that runs.
This holds structurally rather than by escaping — there is no code path where a value is re-parsed,
so nothing downstream has to remember to escape anything.

**`effects` is declared, not inferred.** Nothing about a binary reveals whether it writes: `curl`
POSTs, `jq` and `sed` both take `-i`. So the pack author says. Undeclared means `write`, so an
unclassified command fails closed — and `permission: readonly` becomes enforceable against a fact.
The MCP path still guesses this from the tool name ([#825](https://github.com/delivstat/swarmkit/issues/825)),
which lets `truncate_table` through and stops `send_query`; claim 3 in the demo shows the difference.

**Scopes authorize; the tier only decides whether governance is consulted.** A command skill
declares `iam.required_scopes` exactly like any other skill. The pack contributes the permission
tier. Nothing about governance is new here — the model was never MCP-specific, only named that way.

**Secrets reach a command through `env`, never `argv`.** `{credential.*}` in an argv template is a
schema error. One rule buys three properties that then need no further vigilance: a secret cannot be
model-placed, cannot appear in the audit line recording what ran, and cannot be read from `ps`. The
cost is real — a CLI that only takes a credential as a flag needs a wrapper.

**`requires` is checked at workspace load.** A topology that only runs where a binary happens to be
installed is weaker portable data than one that does not, so a missing binary fails at load with the
binary named, rather than as an exec error four steps into a run.

## The workspace

[`workspace/`](workspace/) declares two packs over the same binary — `json-tools` (`readonly`, both
commands `effects: read`) and `json-editing` (whose one write command is pulled up to `strict`) —
plus an analyst that holds only reads and an editor that holds the gated write.

It requires `jq`. On a machine without it the workspace refuses to load and says so, which is claim
5 of the demo.
