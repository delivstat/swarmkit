# Governed memory

Structured memory that **evolves in place over time** instead of piling up. A growing application's
facts change; governed memory updates the relevant fact rather than appending a duplicate, and every
write is reconciled and audited.

See the design note: [`design/details/governed-memory.md`](https://github.com/delivstat/swarmkit/blob/main/design/details/governed-memory.md).

## The model

- **One canonical row per `(subject, attribute)` key** — the current, trusted value of a fact. The
  key (e.g. `user:alice` × `preferred_language`) is the reconciliation anchor: a later observation
  about the same key updates that row rather than creating a new one.
- **An append-only change-log** — every mutation (`new` / `reinforce` / `update` / `refine` /
  `contradict`). The memory row is mutable; the *record of change* never is, so update-in-place and
  the append-only audit invariant (design §8.3) coexist. Any fact is readable `as_of` a past time.
- **Confidence decay** — a fact's effective confidence fades with time since it was last reinforced
  (per-type half-life). Stale facts rank **down** in retrieval without being deleted; a fact that
  keeps being observed stays strong.

## The governed write path

An agent proposes candidates; it never overwrites. Each candidate is reconciled:

| Op | When | Effect |
|---|---|---|
| `new` | no memory for the key | insert |
| `reinforce` | identical value restated | bump recency + confidence, **no new row** |
| `update` | changed value, a legitimate evolution | supersede the value in place |
| `refine` | changed value that adds detail | merge into the existing memory |
| `contradict` | conflicts with a trusted, high-confidence memory | **quarantine** the candidate for a human curator — the trusted value is never overwritten |

`new` / `reinforce` / `update` are decided deterministically (no LLM). The `refine` / `contradict`
discrimination is the **`memory-reconcile`** decision skill (`category: decision`), which runs only
on a changed value. Contradictions park on a quarantine queue — the one hard human gate in the
memory path — where a curator accepts (apply as an update) or rejects (discard) them.

## Retrieval

Search is **relevance-ranked**, not substring: a local TF-IDF score by default (no keys), or cosine
similarity when an `Embedder` is wired into the store (a plain callable — a local model or an MCP
embedder plugs in with no vendor lock-in). Effective confidence is the secondary signal; an empty
query lists all facts by confidence and recency.

## Using it

Add the `governed-memory` persistence skill to a writer agent; the runtime routes its proposed
candidates through the governed write path at `post_output`. Add the `memory-reconcile` skill to the
workspace to enable refine/contradict (otherwise a changed value deterministically updates). The
[`knowledge-curator`](https://github.com/delivstat/swarmkit/blob/main/reference/topologies/knowledge-curator.yaml)
reference topology wires an ingester (proposes) → reconcile-judge (`memory-reconcile`) → publisher.

### CLI

The `swarmkit memory` commands and the serve `/memory` endpoints resolve the **same** store
(`WorkspaceRuntime.governed_memory`) and emit the same JSON.

| Command | Description |
|---|---|
| `swarmkit memory search "<query>" -w <ws>` | Relevance-ranked search (empty query lists all by confidence) |
| `swarmkit memory search ... --type <t> --limit N --json` | Filter by memory type; JSON output |
| `swarmkit memory get <subject> <attribute> -w <ws> --history` | Current value + the append-only change timeline |
| `swarmkit memory quarantine -w <ws>` | List quarantined contradictions (`--status accepted\|rejected`) |
| `swarmkit memory resolve <id> --by <curator> --accept\|--reject -w <ws>` | Resolve a quarantined contradiction |

### Serve endpoints

| Method + path | Description |
|---|---|
| `GET /memory?query=&type=&limit=` | Relevance-ranked search |
| `GET /memory/item?subject=&attribute=&history=` | Current value (+ history) for a key |
| `GET /memory/quarantine?status=` | Quarantined contradictions |
| `POST /memory/quarantine/{id}/resolve` | `{resolved_by, accept}` — resolve a contradiction |

## Demos

- `just demo-governed-memory` — a fact evolves in place; a contradiction is quarantined and the curator resolves it.
- `just demo-governed-memory-search` — lexical vs embedding relevance ranking.
- `just demo-governed-memory-run` — a live compiled run writes governed memory via the persistence skill.
- `just demo-governed-memory-cli` — the `swarmkit memory` CLI over a seeded workspace.
- `just demo-knowledge-curator` — the reconcile skill + curator topology.
