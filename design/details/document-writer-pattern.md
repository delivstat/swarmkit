# Document Writer Pattern

**Status:** implemented (Sterling workspace)
**Design ref:** §6 (skills), §18 (MCP integration)

## Goal

Enable agents to produce formatted documents (DOCX, PDF) that follow a sample document's structure and styling — not just raw markdown.

## Non-goals

- WYSIWYG document editing (agents write content, not layout)
- Template engines with placeholders (the LLM understands structure from reading the sample)

## Pattern

```
Sample DOCX ──→ MarkItDown ──→ Markdown (structure visible to LLM)
                                    ↓
Research data + structure ──→ LLM generates content as Markdown
                                    ↓
Markdown draft ──→ Pandoc (with --reference-doc=sample.docx) ──→ Output DOCX
```

### Three MCP servers involved

1. **MarkItDown** (`markitdown-mcp`) — reads the sample document into markdown so the LLM can see the section structure, heading hierarchy, and content patterns.

2. **Filesystem** (`server-filesystem`) — writes the markdown draft to disk (intermediate artifact, human-reviewable).

3. **Pandoc** (`mcp-pandoc`) — converts the markdown draft to DOCX/PDF/HTML. The `reference_doc` parameter passes the sample DOCX so pandoc copies its styles (fonts, headers, margins, page layout) into the output.

### Why this works

- LLMs produce good markdown but cannot produce binary formats (DOCX, PDF) directly.
- Pandoc's `--reference-doc` preserves the visual identity of the sample without the LLM needing to understand styles, fonts, or page layout.
- The markdown draft is a human-reviewable intermediate — if the content is wrong, fix the markdown and re-convert.

### Archetype design

The document writer is a **focused worker** under a coordinator, not a standalone agent. It receives pre-researched data from sibling agents (Jira, config, docs, developer) and its only job is formatting that data into a document.

Skills needed:
- `read-document` (MarkItDown) — read the sample
- `read-review-doc` / `list-review-docs` — access sample docs in the review directory
- `write-notes` (filesystem) — save the markdown draft
- `convert-document` (pandoc) — produce the final format

### Workspace configuration

```yaml
mcp_servers:
  - id: pandoc
    transport: stdio
    command: ["uvx", "mcp-pandoc"]
    permission: open
```

## Usage example

User: "Create a design document for JIRA-1234 following the format in review-docs/sample-design.docx"

Flow:
1. Architect delegates to jira-researcher, config-analyst, docs-researcher, developer (steps 1-4)
2. Architect delegates to document-writer with all research + sample path
3. Document-writer reads sample-design.docx via MarkItDown
4. Document-writer writes BOPIS-Design.md to notes directory
5. Document-writer calls convert-document with reference_doc=sample-design.docx
6. Output: BOPIS-Design.docx in notes directory with matching styling

## Test plan

1. Place a sample DOCX in review-docs
2. Run `swarmkit run sterling-assistant` with a document creation prompt
3. Verify: markdown draft saved, DOCX output created, styling matches sample
