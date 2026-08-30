# reference/command-packs/

Command packs you can copy into a workspace. Each file carries one pack under `command_pack:`.

## How to use one

Copy the `command_pack:` block into your `workspace.yaml`:

```yaml
command_packs:
  - id: json-tools
    requires: [{ binary: jq, version: '>=1.6' }]
    permission: readonly
    commands:
      - id: query
        argv: [jq, '-r', '{filter}', '{file}']
        effects: read
```

Then grant it in a topology or archetype:

```yaml
skills:
  - pack:json-tools     # every read command in the pack
```

## Why these are copied rather than registered

**Declaring a pack is not granting it.** A pack in the workspace is inert until a topology asks for
it, and that grant is the audit step — which is why bundling costs nothing in surface area. What it
saves is everyone writing the same `jq` pack by hand.

They are reference artifacts, not a runtime dependency: copy, edit, keep. If you change the argv or
tighten a timeout, that is the intended use.

## What is in here

| pack | binary | tier | what it gives you |
| --- | --- | --- | --- |
| [`json-tools`](json-tools.yaml) | `jq` | `readonly` | query, structured query, keys, validate |
| [`text-tools`](text-tools.yaml) | `rg` | `readonly` | search, files-matching, count-matches |

Both are read-only throughout, so `permission: readonly` is enforceable rather than aspirational —
every command declares `effects: read`, and an undeclared command would default to `write` and be
denied.

## Writing your own

Two rules carry most of the weight, both from `design/details/command-packs.md`:

**`argv`, never a shell.** A `{placeholder}` is filled with the *value* of an argument and stays one
argv entry, so a value containing `;` or `$(…)` is inert. There is no code path that re-parses it.

**Declare `effects` on every command.** Nothing about a binary reveals whether it writes — `curl`
POSTs, `jq` and `sed` both take `-i`. Undeclared means `write`, so an unclassified command fails
closed; a bulk `pack:` grant carries reads only.
