---
title: Context compression — a pluggable ContextCompressor seam (with a Sterling spike)
description: Cut tokens on the read-side (tool/MCP/RAG/log/history) via a pluggable, reversible, governed ContextCompressor at the tool-output boundary. NOT on the audit or structured inter-agent paths. Measured on Sterling's real consolidated JSON: 57%/2.3x lossless, of which 29% is free (just minify).
tags: [cost, tokens, compression, mcp, governance, sterling, rag]
status: proposal (spiked)
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

## Per-project applicability

- **Sterling → yes (measured on the real consolidated JSON).** **57% / 2.3x off
  as-written**, lossless — of which **29% is free** (the ingestion emits pretty-printed
  JSON; just minify). Best first integration; do the free minify now, columnar via the
  seam, and re-spike javadocs/logs (should compress more).
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
   workspaces for the same pretty-print waste.
2. Build the **pluggable `ContextCompressor` seam** at the tool-output/RAG boundary —
   reversible, governed, audited; never on the audit or structured-comms paths.
3. Ship a **built-in deterministic columnar/JSON compactor** (lossless; on Sterling,
   1.63x over minified / 2.3x over as-written; zero heavy deps) as the default;
   headroom-style learned/lossy as an optional backend for lossy-tolerant surfaces
   (logs/RAG/javadocs) only.
4. Adopt **KV-cache prefix stability** independently (lossless, M14).
5. Re-spike **javadocs / logs** (the lossy-tolerant surfaces) before picking a lossy backend.

## Open questions

1. Compressor placement: tool-loop hook vs ModelProvider boundary vs external proxy
   (lean tool-loop hook — governable + auditable).
2. Retrieve-tool governance: scope + audit shape for `*_retrieve` recall.
3. Per-content-type policy: which surfaces are lossless-only vs lossy-ok, as workspace config.
