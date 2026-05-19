---
title: Document reader MCP server
description: Two-server architecture for document understanding — MarkItDown for text extraction, swarmkit-docs-reader for visual image analysis and structured diagram parsing.
tags: [mcp, knowledge, skills, m8]
status: active
---

# Document reader architecture

**Scope:** MCP integration, document understanding
**Design reference:** §18 (MCP integration)
**Status:** implemented in M8

## Architecture: two complementary MCP servers

Document understanding requires two capabilities that map to different tools:

### 1. MarkItDown (Microsoft, open-source)

Handles document → markdown conversion with embedded images preserved as data URIs.
The model receives coherent markdown where images appear inline with surrounding text context.

```yaml
mcp_servers:
  - id: markitdown
    transport: stdio
    command: ["uvx", "markitdown-mcp"]
    permission: open
```

**Formats:** PDF, DOCX, XLSX, PPTX, HTML, EPUB, CSV, JSON, XML, images (OCR), audio (transcription).

**Key advantage:** images embedded in documents stay in context. A diagram on page 5 of a PDF appears inline with the text that references it ("Figure 3 shows the data flow..."). This is critical for coherent understanding.

**Tool:** `convert_to_markdown(uri)` — accepts file:// and http:// URIs.

### 2. swarmkit-docs-reader

Provides capabilities MarkItDown doesn't: visual image return for multimodal models, draw.io diagram parsing, and file discovery.

```yaml
mcp_servers:
  - id: docs-reader
    transport: stdio
    command: ["swarmkit", "docs-reader"]
    permission: open
```

**Tools:**

| Tool | Purpose |
|------|---------|
| `view_image` | Returns raw image as MCP `ImageContent` — the model SEES the image visually |
| `read_drawio` | Parses draw.io XML → structured node/edge descriptions |
| `read_svg` | Extracts text elements from SVG diagrams |
| `read_csv` | CSV → markdown table with truncation |
| `read_text` | Plain text with line numbers and range support |
| `list_files` | File discovery with glob patterns |

## Why two servers

MarkItDown converts documents to text (with embedded images as data URIs). This works for models that process data URIs in markdown. But for true visual understanding — where the model needs to SEE a diagram and reason about spatial relationships, colours, arrows — the image must be passed as MCP `ImageContent`. That's what `view_image` does.

The split also follows SwarmKit's "skills are the extension primitive" principle. An agent with `skills: [read-document]` gets MarkItDown. An agent with `skills: [view-image]` gets visual analysis. An agent with both can read documents AND see their embedded diagrams.

## Image flow (Option C — skill-driven)

Images flow through skill tool results, not through state injection:

```
Agent calls view_image("architecture.png")
  → docs-reader MCP returns [TextContent, ImageContent]
  → compiler detects ImageContent in tool result
  → adds image ContentBlock to tool_result message
  → model sees the image in its next turn
```

No `--image` CLI flag, no `image_paths` in state, no broadcasting.
The agent decides when to look at an image by calling the tool.
