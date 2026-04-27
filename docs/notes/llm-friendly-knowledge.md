---
title: LLM-friendly knowledge
description: Conventions for keeping Swael documentation and schemas queryable by LLMs. Docs are consumed primarily by LLMs on behalf of humans — design for that consumption model first.
tags: [discipline, documentation, llm]
status: active
---

# LLM-friendly knowledge

Users, reviewers, and contributors consume Swael's documentation **primarily through LLMs** — Claude.ai, ChatGPT, Cursor, Claude Code, local Ollama, whatever. Nobody reads a full design doc cover-to-cover anymore; they ask an LLM for a specific answer. That's the realistic model, not a concession.

This note is the operational consequence: every docs decision optimises for LLM retrieval first, human linear reading second.

## The delivery chain

An LLM answering a user's Swael question has access to the corpus through one of:

1. **Public crawl.** LLMs trained with web access see the repo via `llms.txt` and GitHub. Low-effort, high-reach — but only works once the corpus is public and the training window is recent enough.
2. **User-initiated paste.** `swael knowledge-pack` (task #24) bundles the corpus into a paste-ready blob. Works with any LLM, no infrastructure.
3. **MCP server.** Task #25 will expose the corpus as an MCP server so Claude Code, the future Swael UI, or any MCP client can query it live.

All three paths work only if the corpus itself is LLM-friendly. That's what this note covers.

## Rules

### Markdown, not binary

The design doc lives in markdown (`design/Swael-Design-v0.6.md`). The original `.docx` is archived under `design/archive/` for historical reference only. Never add new authoritative content in a binary format; never require an extraction step to read Swael docs.

### Frontmatter on design docs

Every `design/*.md` and `docs/notes/*.md` starts with YAML frontmatter:

```yaml
---
title: <short title for retrieval>
description: <1-2 sentences — this is what the LLM sees in search results>
tags: [schema, topology, m0, …]
status: draft | in-review | approved | implemented | active
---
```

The fields are retrieval metadata. Keep them short, concrete, and stable. Don't invent new tags casually — tag drift defeats retrieval.

### Short, self-contained sections

LLM retrieval pulls chunks. A section that says "see §8.6" forces the retrieval system to pull another chunk before the user's question is answerable. Prefer:

- Short headings. Two to four level deep, no more.
- Concrete examples after every claim, inline.
- Tables for enumerated info (enums, mappings, defaults) — easier to retrieve and quote.
- Self-contained explanations. If you must cross-reference, name the concept, not just the section number. "See §8.6 (judicial tiering model)" is recoverable; "see §8.6" is not.

### Errors are docs

When the runtime prints an error, an LLM will quote it verbatim to a user. Write errors like documentation:

- What happened (in the user's vocabulary, not the schema's).
- Why it happened (which rule, which artifact, which field).
- What to try (specific, not "check the docs").

See task #23 — the first concrete landing of this rule is `swael validate` human-readable errors.

### `llms.txt` is the index

The repo's `llms.txt` (at the root, per llmstxt.org) points LLMs at the high-leverage docs. When you add a design note, a discipline note, or a new top-level doc, update `llms.txt`. It's the catalogue crawlers actually see.

### Stable schema URLs

JSON Schemas declare `$id` URLs (e.g. `https://schemas.swael.dev/v1/topology.schema.json`). Tools that try to dereference a schema follow those URLs. Until the domain exists and serves the canonical files, any tool doing remote `$ref` resolution will fail — noted as a known limitation of the current state; fixing it is on the Milestone 10 track (may get promoted earlier if it blocks a user).

## Anti-patterns

- **Long narrative paragraphs**. LLMs handle them fine but retrieval is worse than structured sections + tables + examples.
- **Implicit cross-references.** "As mentioned above." Retrieval doesn't have "above."
- **Tribal knowledge in commit messages.** Commit messages aren't part of the standard LLM corpus. If a decision is important, it belongs in a design note or discipline note.
- **Screenshots for reference info.** LLMs can read them but with higher error rate than text. Prefer markdown tables or code fences.
- **Custom retrieval tooling inside the framework.** `llms.txt` + MCP server + structured markdown is enough. Don't build a bespoke search engine.

## Scope boundary

This note covers *documentation* artifacts. It does not cover runtime artifacts (topology YAMLs, skill definitions) — those have their own schema-driven shape and are indirectly LLM-friendly because the schemas are explicit. The knowledge base is docs + schemas + examples, all three.

## See also

- `design/Swael-Design-v0.6.md` §3.4 The first-run promise
- `docs/notes/usability-first.md` — the per-PR friction-reduction checklist
- `docs/notes/schema-change-discipline.md` — schemas are LLM-friendly by default; this is how we keep them that way
- Tasks #23, #24, #25 — the concrete landings of this note
