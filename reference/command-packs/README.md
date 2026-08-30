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

| pack | needs | tier | what it gives you |
| --- | --- | --- | --- |
| [`file-tools`](file-tools.yaml) | **nothing** — coreutils | `readonly` | read-file, head, tail, count-lines, list-files |
| [`json-tools`](json-tools.yaml) | `jq >=1.6` | `readonly` | query, query-json, keys, validate |
| [`text-tools`](text-tools.yaml) | `rg` (ripgrep) | `readonly` | search, files-matching, count-matches |

**Start with `file-tools`** if you just want to see a pack work. It is the only one that runs on a
bare machine — `jq` and `rg` are worth installing, but neither ships with an OS, and a reference
artifact that needs `apt` first is a poor on-ramp.

Both are read-only throughout, so `permission: readonly` is enforceable rather than aspirational —
every command declares `effects: read`, and an undeclared command would default to `write` and be
denied.

## What a pack does not confine

Worth knowing before granting `file-tools`, and true of every pack: **a command pack has no path
sandbox.** `cwd` sets the working directory, and an absolute path escapes it. `read-file` will read
anything the runtime process can read.

That is a deliberate boundary rather than an oversight — confining paths means understanding each
binary's argument grammar, and a pack that half-confines is worse than one that says it does not.
What bounds a pack instead:

- **The grant.** A pack is inert until a topology asks for it, so the question is which agents hold
  it, not what the binary could theoretically do.
- **The permission tier**, which is enforceable because every command declares `effects`.
- **`iam.required_scopes` on the skill**, which is what actually authorizes the call.

If you need real filesystem confinement, run the workspace in a container — the same answer
SwarmKit gives for harness executors.

## Writing your own

Two rules carry most of the weight, both from `design/details/command-packs.md`:

**`argv`, never a shell.** A `{placeholder}` is filled with the *value* of an argument and stays one
argv entry, so a value containing `;` or `$(…)` is inert. There is no code path that re-parses it.

**Declare `effects` on every command.** Nothing about a binary reveals whether it writes — `curl`
POSTs, `jq` and `sed` both take `-i`. Undeclared means `write`, so an unclassified command fails
closed; a bulk `pack:` grant carries reads only.
