---
title: Context compression — a pluggable ContextCompressor seam (with a Sterling spike)
description: Cut tokens on the read-side (tool/MCP/RAG/log/history) via a pluggable, reversible, governed ContextCompressor at the tool-output boundary. NOT on the audit or structured inter-agent paths. Measured on real Sterling CDT data: ~1.5x incremental even after ingestion.
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

## Spike: Sterling CDT data (real measurement)

Sterling is the densest tool-output workload (1006 javadocs, log dumps, 495-table CDT
XML, ChromaDB+FTS5 RAG), so it's the right spike target. Measured with `tiktoken`
(`cl100k_base`) over **183 populated CDT tables / 34,727 rows / 5.5M raw tokens** from a
real MC1 CDT dump. CDT rows repeat every attribute *name* per record (plus empty attrs)
— classic array-of-dicts redundancy.

| Form | Tokens | vs raw |
| --- | --- | --- |
| Raw CDT XML | 5,525,061 | — |
| Array-of-dicts JSON (≈ what ingestion emits) | 4,838,641 | **12%** off |
| **Columnar JSON** (keys declared once) | 3,174,297 | **43%** off |

**Key finding (honest):** Sterling's ingestion already consolidates XML→JSON, but that
only removes ~12% of the *tokens*. A content-aware **columnar** compressor cuts a
**further 34% (1.52×) on top of the consolidated JSON** — and it's **lossless on the
field values** (it only declares keys once instead of per-row). That incremental ~1.5×
on config data is the real value of the seam; it is *more modest* than headroom's
60–95% headlines, which come from code/log/RAG content that compresses harder.

Caveats:
- This measured the raw CDT XML + an *approximation* of Sterling's consolidated JSON.
  The Sterling **workspace ingestion scripts** (which emit the actual consolidated JSON)
  weren't reachable from this session — **re-run the script below on the real
  consolidated JSON for the production number** (and on javadocs/logs/RAG chunks, which
  should compress more).
- The ~1.5× columnar gain is **lossless**. Going further (learned/semantic, lossy)
  risks corrupting **config values Sterling needs exact** — so for CDT/config, stop at
  lossless columnar; reserve lossy+retrieve for logs/RAG prose.

### Reproducible spike (run on the real consolidated JSON)

```python
# uv run --with tiktoken python this.py <glob-of-consolidated-json-or-cdt-xml>
# Measures raw vs array-of-dicts vs columnar token counts; reports incremental gain.
# (CDT-XML variant used for the numbers above; point it at consolidated JSON for prod.)
```
(Full script in the PR description / spike notes — ~30 lines: parse rows → union keys →
columnar JSON → tiktoken counts.)

## Per-project applicability

- **Sterling → yes.** ~1.5× lossless on CDT config even post-ingestion; likely more on
  javadocs/logs/RAG. Best first integration. Spike the real consolidated JSON next.
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

1. Build the **pluggable `ContextCompressor` seam** at the tool-output/RAG boundary —
   reversible, governed, audited; never on the audit or structured-comms paths.
2. Ship a **built-in deterministic columnar/JSON compactor** (lossless, ~1.5× on
   Sterling config, zero heavy deps) as the default; headroom-style learned/lossy as an
   optional backend for lossy-tolerant surfaces (logs/RAG) only.
3. Adopt **KV-cache prefix stability** independently (lossless, M14).
4. **Spike the real Sterling consolidated JSON** (and javadocs/logs) before committing
   to a backend.

## Open questions

1. Compressor placement: tool-loop hook vs ModelProvider boundary vs external proxy
   (lean tool-loop hook — governable + auditable).
2. Retrieve-tool governance: scope + audit shape for `*_retrieve` recall.
3. Per-content-type policy: which surfaces are lossless-only vs lossy-ok, as workspace config.
