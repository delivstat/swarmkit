---
title: Document reader MCP server
description: MCP server for extracting text and structure from PDFs, DOCX, Excel, images, and diagrams. Enables agents to understand documents without multimodal models.
tags: [mcp, knowledge, skills, m8]
status: draft
---

# Document reader MCP server

**Scope:** new MCP server, optional dependency
**Design reference:** §18 (MCP integration)
**Status:** implementing as part of M8

## Problem

Agents in the Sterling OMS workspace need to understand documents: API javadocs (PDF), solution designs (DOCX), data mappings (Excel), architecture diagrams (draw.io/SVG), and flow diagrams (images). Currently they can only search pre-ingested text via ChromaDB/FTS5. When a user points them at a new document, they can't read it.

Claude Code works for this use case because it has native multimodal support and can read files directly. SwarmKit agents need an equivalent capability as an MCP tool.

## Design

A standalone FastMCP server with one tool per document type:

| Tool | Input | Output | Library |
|------|-------|--------|---------|
| `read_pdf` | file path, optional page range | extracted text with page markers | PyMuPDF (`pymupdf`) |
| `read_docx` | file path | structured text with heading hierarchy | `python-docx` |
| `read_excel` | file path, optional sheet name | markdown table(s) | `openpyxl` |
| `read_image` | file path | OCR text extraction | Pillow + `pytesseract` (optional) |
| `read_drawio` | file path | parsed node/edge descriptions | XML parsing (stdlib) |
| `read_csv` | file path, optional row limit | markdown table | `csv` (stdlib) |
| `read_text` | file path | raw text with line numbers | stdlib |

All tools accept a `path` argument (resolved relative to workspace root or absolute). Output is always text — suitable for any LLM, no multimodal needed.

## Key decisions

1. **Text extraction, not rendering.** The server extracts text and structure from documents. For true visual understanding (e.g. reading a chart from an image), multimodal model support in ModelProvider is needed separately.

2. **Optional dependencies.** The server works with whatever libraries are installed. Missing `pymupdf`? `read_pdf` returns a clear error telling the user to `pip install pymupdf`. No hard dependency on any document library.

3. **Workspace-relative paths.** The server resolves paths relative to the workspace root (passed as `--workspace` or `SWARMKIT_WORKSPACE`). This matches how other MCP servers work.

4. **Output truncation.** Large documents are truncated with a message showing total size and a hint to use page/row range arguments. Default limits: 50 pages for PDF, 100 rows for Excel/CSV, 500KB for text files.

## Non-goals

- Multimodal/vision model support (separate PR — ModelProvider change)
- Document indexing or search (that's the knowledge server's job)
- Document writing/editing
- Proprietary format support (e.g. Visio .vsdx)

## Usage in workspace.yaml

```yaml
mcp_servers:
  - id: docs-reader
    transport: stdio
    command: ["uv", "run", "swarmkit-docs-reader"]
    permission: open  # read-only, no governance needed
```

## Skill definitions

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  name: read-document
  description: Read and extract text from PDF, DOCX, Excel, CSV, images, and diagrams
  category: capability
  provenance:
    source: swarmkit-builtin
implementation:
  type: mcp_tool
  server: docs-reader
  tool: read_pdf  # or read_docx, read_excel, etc.
```

In practice, a single `read-document` composed skill wrapping all the individual tools would be more ergonomic.
