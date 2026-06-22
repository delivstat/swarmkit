---
title: Context compression — a pluggable ContextCompressor seam (with a Sterling spike)
description: Cut tokens on the read-side (tool/MCP/RAG/log/history) via a pluggable, reversible, governed ContextCompressor at the tool-output boundary. NOT on the audit or structured inter-agent paths. Measured on Sterling's real consolidated JSON: 57%/2.3x lossless, of which 29% is free (just minify).
tags: [cost, tokens, compression, mcp, governance, sterling, rag]
status: slice 3 built (per-surface policy + reversible headtail + context_retrieve + OTel + doc lever)
---

# Context compression

## Why

Token cost scales with a multi-agent system's read-side: tool/MCP outputs, RAG chunks,
logs, and growing history all re-enter agent context, often wastefully. Tools like
[headroom](https://github.com/chopratejas/headroom) show 60–95% input-token cuts on
tool-heavy workloads via content-type-aware compression (JSON dedup, AST-aware code
stripping, learned prose distillation), KV-cache prefix alignment, and **reversible**
compression (originals cached; the model calls a retrieve tool if it needs them).

This is significant to SwarmKit (more agents → more context passing → more tokens) —
but *where* it applies matters, so this note defines a narrow, safe seam rather than a
blanket proxy.

## The seam: `ContextCompressor` at the tool-output boundary

A pluggable compressor — same shape as `ModelProvider` / `GovernanceProvider`
(invariant #4: no lock-in) — applied at the **tool-loop / RAG interception point**
(the same uniform seam governance + OTel + eval already use):

```
MCP/tool result ─▶ GovernanceProvider (gate) ─▶ ContextCompressor (compress) ─▶ agent context
                                                      │ originals cached
                                                      ▼ headroom_retrieve (governed MCP tool)
```

- **Pluggable backend** (headroom is one; a deterministic columnar/JSON compactor is
  the built-in default). Off by default; opt-in per workspace.
- **Reversible** — compressed-out detail is cached; the agent can recover it via a
  retrieve tool, which is itself a **governed MCP call** (routed through
  `GovernanceProvider`) and **audited**.
- **Lossy-aware placement** — applies to the *read-side bulk* only.

### Where it must NOT apply (the push-back)

- **The audit / governance path** — audit must stay complete + append-only (§8.3);
  never compress what's recorded.
- **The structured inter-agent contract** — [[structured-inter-agent-communication]]
  already compresses agent-to-agent messages *deterministically and losslessly*
  (JSON/YAML, 55–87% reduction). Lossy compression there risks dropping signal the next
  agent needs. **Compress what agents READ (tool/RAG/history); keep the structured
  contract for what agents SAY.**
- **Tiny-model / edge paths** — a lossy+retrieve scheme needs a model capable of knowing
  to call retrieve; small local models (e.g. Minder's 3B) won't, and the dependency
  weight is wrong for an edge box.

### One lossless win to take regardless

**KV-cache prefix stability** (headroom's CacheAligner idea): keep prompt prefixes
stable so provider KV-caches actually hit. Pure win, no lossy risk — fits M14 (cost
optimization) independent of any compressor backend.

## Spike: Sterling — the REAL consolidated JSON (measured)

Ran Sterling's actual ingestion — `examples/sterling-oms/workspace/scripts/ingest-cdt.py
<cdt-dir> --output <out>` (via `ingest-all.py --only cdt`) — on a real MC1 CDT dump, then
measured the **consolidated JSON it emits** (the LLM-facing artifact, not the raw XML)
with `tiktoken` (`cl100k_base`). `ingest-cdt.py` is a *semantic* consolidator (services /
pipelines / transactions / statuses / common-codes / per-table JSON), so its output is
already domain-distilled.

**503 consolidated JSON files, 23.4M tokens as-written:**

| Form | Tokens | vs as-written |
| --- | --- | --- |
| As-written (pretty-printed, indent=2) | 23,375,381 | — |
| **Minified** (strip whitespace only) | 16,490,773 | **29% off** |
| **Minified + columnar** (array-of-dicts → keys-once) | 10,123,441 | **57% off (2.3x)** |

**Two findings, both real + lossless:**

1. **The biggest single win is free: minify.** Sterling's ingestion writes pretty-printed
   JSON, so **~29% of the tokens are indentation/whitespace.** If that JSON reaches the
   model as-written, `json.dumps(…, separators=(",",":"))` recovers 29% at zero risk — a
   **one-line change in the ingestion / serving path**, no ContextCompressor needed.
   *(Take this regardless of the seam.)*
2. **Content-aware columnar adds another 1.63x on top of minified** (38.6%), for
   **57% / 2.3x off as-written** — lossless (arrays-of-uniform-dicts rewritten to
   `{columns, rows}`, values preserved). This is the seam's value on Sterling config, and
   it's *stronger* than the earlier raw-XML approximation suggested.

Caveat: beyond lossless columnar, learned/semantic (lossy) compression risks corrupting
**config values Sterling needs exact** — stop at lossless for CDT; reserve lossy+retrieve
for logs / RAG prose / javadocs (which should compress more, per headroom's benchmarks).

### Reproducible spike

`ingest-cdt.py <cdt-dir> --output /tmp/cdt-index`, then for each `*.json`,
`tiktoken`-count as-written vs minified vs a recursive `columnar()` rewrite of
arrays-of-uniform-dicts (script ~25 lines, in the PR body). Re-run on javadocs/logs for
those surfaces.

## Docs / RAG surface (Confluence + product/project docs) — a *different* profile

The other big Sterling token sink is doc content fetched into context (Confluence pages,
product/project docs). It's **prose**, not array-of-dicts — so the **lossless 2.3× from
CDT does NOT transfer.** Measured the reachable `data-model/*.md` corpus: **856 files,
824K tokens**, and the content is overwhelmingly *unique* prose (per-column descriptions)
— cross-file boilerplate is only ~9 repeated header lines. There's no structural
redundancy to dedup losslessly.

So docs need different levers (and `ingest-docs.py` already does the easy lossless ones —
HTML→text strip + chunking into ChromaDB, so there's no query-time HTML win left):

1. **Retrieval selection — the biggest lever, and lossless.** The per-query cost is
   `K chunks × chunk size`. Feeding fewer, higher-signal chunks (better top-K, score
   filtering — headroom's "intelligent context") cuts tokens *with zero information loss*
   because you simply retrieve less. This is a RAG-tuning win, not a compressor, and it's
   where most of the doc savings live.
2. **Lossy prose compression of the *retrieved* chunks** (headroom's learned model) —
   a moderate further cut, but **lossy on technical descriptions** (dropping a nuance in
   an API/column description can mislead). Use **only with reversible retrieve** so the
   agent can pull the full chunk back. This is the lossy-tolerant surface the seam
   reserves lossy for.

Honest gap: I didn't put a single "% off" on lossy prose — it needs the learned model
(not runnable here, git+Rust+ONNX). The measurable truths are: the corpus is large (and
product/Confluence is likely larger than data-model's 824K), lossless-structural is
~nil on prose, and the dominant lossless lever is **retrieval selection**.

## Per-project applicability

- **Sterling → yes, two surfaces.** *Config (CDT JSON):* **57% / 2.3x off as-written**,
  lossless — of which **29% is free** (the ingestion pretty-prints; just minify). *Docs
  (Confluence + product/project, RAG):* a large prose corpus (data-model alone ~824K
  tokens) where the win is **retrieval selection (lossless) + reversible lossy chunk
  compression**, not structural. Best first integration overall.
- **Vedanta → medium, cautious.** RAG-chunk *selection* (score filtering) helps; the
  content is **scripture**, so *lossy* prose compression risks theological precision —
  prefer lossless selection, keep retrieve, avoid lossy value compression.
- **Minder → no.** Small local models + edge box; token cost isn't the bottleneck
  (local compute is), Frigate events are already tiny + structured, and lossy+retrieve
  won't work on a 3B. Don't add it.

## Headroom-specific caveat

The PyPI package `headroom` (0.2.7) is an **unrelated** project (a command-gen CLI), not
the compression library — the real one is GitHub-only and needs a git install with Rust
(maturin) + an ONNX model. So treat headroom as a *reference design + optional backend*,
not a drop-in dependency; the built-in deterministic columnar/JSON compactor (measured
above) needs no heavy deps and delivers the lossless tier.

## Recommendation

1. **Minify first (free, lossless, now).** Sterling's consolidated JSON is
   pretty-printed → ~29% of tokens are whitespace; emit/serve it compact
   (`separators=(",",":")`). One-line ingestion fix, no seam required. Audit other
   workspaces for the same pretty-print waste. **Done** — Sterling ingestion minified
   (29% banked).
2. Build the **pluggable `ContextCompressor` seam** at the tool-output/RAG boundary —
   reversible, governed, audited; never on the audit or structured-comms paths.
   **Done (slice 1)** — `swarmkit_runtime.compression`: `ContextCompressor` Protocol +
   `maybe_compress_tool_result` gate wired into the tool loop, active per-run via
   `set_active_compressor`/`build_compressor` (mirrors `set_active_trace`). Opt-in,
   off by default; the gate never inflates and never raises into a run. **Slice 2** adds
   the declarative `context_compression:` workspace-schema block (env overrides) — see
   Config below.
3. Ship a **built-in deterministic columnar/JSON compactor** (lossless; on Sterling,
   1.63x over minified / 2.3x over as-written; zero heavy deps) as the default;
   headroom-style learned/lossy as an optional backend for lossy-tolerant surfaces
   (logs/RAG/javadocs) only. **Done (slice 1)** — `ColumnarCompressor`
   (minify + array-of-uniform-dicts → `{columns, rows}`), enabled with
   `SWARMKIT_CONTEXT_COMPRESSION=columnar`.
4. **Docs/RAG: tune retrieval first (lossless), then reversible lossy chunk compression.**
   The biggest doc win is feeding fewer/higher-signal chunks per query (score filtering),
   which loses no information; lossy prose compression of retrieved chunks is the
   secondary, reversible lever for that surface.
5. Adopt **KV-cache prefix stability** independently (lossless, M14).
6. Re-spike **javadocs / logs / Confluence** (the lossy-tolerant surfaces) before picking
   a lossy backend.

## Config (built)

Declarative per workspace, with env overriding the default per deployment.

```yaml
# workspace.yaml
context_compression:
  backend: columnar           # off (default) | columnar (lossless) | headtail (reversible-lossy) | plugin
  min_bytes: 2000             # payloads smaller than this are left untouched
  overrides:                  # per-surface rules, matched by tool-name and/or server-id glob
    - match: "get-logs*"      # by tool name — logs tolerate lossy + recall
      backend: headtail
      min_bytes: 4000
    - match_server: "frigate" # by MCP server id — applies to every tool on that server
      backend: headtail
    - match: "search-*"       # never compress search results
      backend: "off"
    - match: "*"              # custom/learned backend by class path
      backend: plugin
      backend_class: "my_pkg.compressors.LearnedCompressor"
```

- `SWARMKIT_CONTEXT_COMPRESSION` — `columnar` | `headtail` | `off`. **Overrides** the
  workspace default `backend`. An explicit `off` disables compression entirely (including
  overrides). Unknown values resolve to off (safe).
- `SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES` — **overrides** the default `min_bytes`.

Precedence (default rule): **env → workspace block → default (off / 2000)**. Per-surface
`overrides` come from the workspace block; the first whose `match` (tool-name glob) **or**
`match_server` (MCP-server-id glob) matches wins. Resolved once per run in
`WorkspaceRuntime.run` into a `CompressionPolicy` (`build_policy(cfg)` → `set_active_policy`).

### Per-run isolation

The active policy + original store live in a **`ContextVar`**, not a module global —
asyncio copies the context when a task is created, so concurrent runs in one process
(e.g. jobs under `swarmkit serve`) each see their own policy and store instead of
clobbering shared state. `_active_trace` was migrated to the same `ContextVar` pattern for
consistency (it had the same latent limitation).

### Backends

- **`columnar`** (lossless) — minify + array-of-uniform-dicts → `{columns, rows}`. The
  agent reads the compact form directly; round-trips to the same records.
- **`headtail`** (reversible-lossy, slice 3) — keep head + tail, elide the middle behind a
  `context_retrieve(ref, offset, limit)` marker. The seam stashes the original in a
  per-run in-memory store keyed by the ref. Lossy *at the point of read* but no information
  is destroyed — recall is deferred. For lossy-tolerant surfaces (logs, verbose dumps).
- **`plugin`** (slice 3) — a custom `ContextCompressor` named by `backend_class` (a
  fully-qualified Python class path, same shape as `model_providers`). Imported and
  instantiated at policy-build time; if the class is missing or isn't a `ContextCompressor`,
  it resolves to off (safe). This is how learned/LLM backends register without a runtime
  edit — the Protocol is the extension seam.

### `context_retrieve` — governance + audit (resolves open question #2)

Retrieval reads back content **already delivered** to the agent (compressed), so it is not
a privilege escalation — no grantable scope gates it. The handler still routes through
`governance.evaluate_action(action="context:retrieve:<ref>", scopes_required=∅)` so every
recall is recorded in the audit trail. The tool is only offered to agents when a reversible
backend is active for the run (otherwise it's a misleading tool nothing produces refs for),
and it returns a ranged window so a large original can be paged without re-hitting the
result-truncation cap.

### Observability (slice 3)

Per-result savings are recorded into `RunTrace` (`compression_bytes_in/out`,
`compression_by_backend`), surfaced in the CLI run summary
(`context compression: X -> Y chars (N% off)`), and emitted as OTel metrics
(`swarmkit.compression.bytes_saved.total` counter, `swarmkit.compression.ratio`
histogram).

### Doc/RAG lossless lever (slice 3)

`knowledge.search_docs` gained a `min_score` floor + a relative top-score cutoff so it
returns fewer, higher-signal sections — the lossless retrieval-selection lever (the
biggest doc/RAG win; loses no information).

### Deferred

- **Eject codegen for the policy** — blocked: `swarmkit eject` is itself an
  M9 `_not_implemented` stub; there is no eject pipeline to wire the policy into yet.
- **A concrete learned/LLM lossy backend** — the `plugin` seam + `headtail` reference
  backend are in place; shipping an actual LLM-summarizer backend needs an async
  compression boundary + cost accounting and is its own slice. (The seam is async-agnostic;
  the current `maybe_compress_tool_result` is sync.)
- **Durable (cross-process) original store** — the in-memory store is per-run and correct
  within a process (including `serve`, now that state is `ContextVar`-isolated). A durable
  store is only needed if recall must survive a process boundary, which a single run never
  crosses today.

## Open questions

1. ~~Compressor placement: tool-loop hook vs ModelProvider boundary vs external proxy.~~
   **Resolved** — tool-loop hook (`maybe_compress_tool_result` at the tool-output
   boundary), governable + auditable.
2. ~~Retrieve-tool governance: scope + audit shape for `*_retrieve` recall.~~
   **Resolved** — recall of already-delivered content is not privilege escalation, so no
   grantable scope gates it; it is audited via an empty-scope `evaluate_action` call. See
   "context_retrieve" above.
3. ~~Per-content-type policy: which surfaces are lossless-only vs lossy-ok, as workspace
   config.~~ **Resolved (slice 3)** — `context_compression.overrides`, matched by tool-name
   glob. Surface classification finer than tool-name globs is deferred.
