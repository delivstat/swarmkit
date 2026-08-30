# `permission: readonly` now needs declared effects

**Applies from swarmkit-runtime 1.199.0.** Only affects MCP servers declared
`permission: readonly`. Every other tier is unchanged.

## What changed

`readonly` used to decide whether a tool writes by substring-scanning the tool *name* for
`create|delete|update|write|put|post|set|add|remove|modify|edit|insert|drop|push|send`.

It failed in both directions at once, which is why a longer word list was never the fix:

| tool | old verdict | why |
| --- | --- | --- |
| `get_dataset` | **denied** | contains `set` |
| `read_asset` | **denied** | contains `set` |
| `list_addresses` | **denied** | contains `add` |
| `get_post` | **denied** | contains `post` |
| `truncate_table` | **allowed** | matches nothing |
| `purge_cache` | **allowed** | matches nothing |
| `revoke_token` | **allowed** | matches nothing |
| `wipe_db` | **allowed** | matches nothing |

The vocabulary of destructive verbs is unbounded and per-server. Enumerating it means chasing every
server's naming convention forever, and the failure stays the same shape — quiet permission wherever
the guess missed.

## What to do

Declare effects per tool on the server:

```yaml
mcp_servers:
  - id: warehouse
    transport: stdio
    command: ["uv", "run", "warehouse_server.py"]
    permission: readonly
    effects:
      get_dataset: read
      read_asset: read
      truncate_table: write
```

Resolution order:

1. **The declared `effects` entry.** Authoritative — it is the half the operator controls, and the
   half that cannot change under them when a server is upgraded.
2. **The server's `readOnlyHint` annotation**, if it sends one. Free and useful, but it is the
   server describing itself.
3. **`unknown`** otherwise.

## The breaking part

Under `readonly`, a tool that is `unknown` is now **denied**. Previously it was allowed whenever its
name happened to miss the word list.

This fails closed on purpose. The alternative — allow what we cannot classify — is the behaviour
that let `truncate_table` through a server its author had marked read-only. The denial names the
tool and the field to add, so the fix is one line rather than an investigation.

Servers on `open`, `cautious` or `strict` need no change; `effects` is consulted only by `readonly`.

## See also

- Issue #825, and `design/details/command-packs.md`, where the same `effects` field is declared per
  command and defaults to `write`.
