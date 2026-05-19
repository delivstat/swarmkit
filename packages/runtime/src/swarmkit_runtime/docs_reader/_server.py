"""Document reader MCP server — visual image analysis and structured file parsing.

Complements MarkItDown (which handles PDF, DOCX, Excel, PPTX → markdown).
This server provides tools MarkItDown doesn't: visual image return for
multimodal models, draw.io/SVG diagram parsing, CSV reading, and file discovery.

For document reading, configure the markitdown MCP server alongside this one:

    mcp_servers:
      - id: markitdown
        transport: stdio
        command: ["uvx", "markitdown-mcp"]
      - id: docs-reader
        transport: stdio
        command: ["swarmkit", "docs-reader"]

See ``design/details/document-reader-mcp.md``.
"""

from __future__ import annotations

import csv
import os
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

server = FastMCP("swarmkit-docs-reader")

_workspace_root: Path | None = None

_MAX_CSV_ROWS = 100

_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _resolve_path(path: str) -> Path:
    """Resolve a path relative to workspace root or as absolute."""
    p = Path(path)
    if p.is_absolute():
        return p
    root = _workspace_root or Path.cwd()
    return root / p


def _truncation_notice(actual: int, limit: int, unit: str, hint: str) -> str:
    return (
        f"\n\n--- TRUNCATED: showing {limit} of {actual} {unit}. "
        f"Use {hint} to read a specific range. ---"
    )


# ---- visual image tools (unique to this server) ----------------------------


@server.tool()
def view_image(path: str) -> list[Any]:
    """Return an image as visual content for multimodal models.

    Returns the raw image bytes so a vision-capable model (Claude Sonnet,
    GPT-4o, Gemini) can see and analyze diagrams, charts, screenshots,
    and other visual content directly. This is NOT OCR — the model sees
    the actual image.

    Args:
        path: File path (relative to workspace root or absolute).
    """
    import base64  # noqa: PLC0415

    from mcp.types import ImageContent, TextContent  # noqa: PLC0415

    resolved = _resolve_path(path)
    if not resolved.exists():
        return [TextContent(type="text", text=f"ERROR: file not found: {resolved}")]

    ext = resolved.suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(ext)
    if mime_type is None:
        return [
            TextContent(
                type="text",
                text=f"ERROR: unsupported image format '{ext}'. "
                f"Supported: {', '.join(sorted(_IMAGE_MIME_TYPES.keys()))}",
            )
        ]

    data = base64.standard_b64encode(resolved.read_bytes()).decode("ascii")
    return [
        TextContent(type="text", text=f"Image: {resolved.name} ({ext}, {len(data)} bytes base64)"),
        ImageContent(type="image", data=data, mimeType=mime_type),
    ]


# ---- structured parsing tools (not covered by MarkItDown) ------------------


@server.tool()
def read_drawio(path: str) -> str:
    """Parse a draw.io XML file and describe its nodes and connections.

    Works with both .drawio and .drawio.xml files. Extracts shapes,
    labels, and connections into a structured text description.

    Args:
        path: File path (relative to workspace root or absolute).
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"ERROR: file not found: {resolved}"

    try:
        tree = ET.parse(resolved)
    except ET.ParseError as exc:
        return f"ERROR: cannot parse XML: {exc}"

    root = tree.getroot()
    return _parse_drawio_xml(root, resolved.name)


def _parse_drawio_xml(root: ET.Element, filename: str) -> str:
    """Extract nodes and edges from draw.io XML."""
    parts: list[str] = [f"# {filename} (draw.io diagram)\n"]

    cells = root.findall(".//mxCell") or root.findall(".//{*}mxCell")
    if not cells:
        return f"# {filename}\n\n(no diagram elements found)"

    nodes: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []

    for cell in cells:
        cell_id = cell.get("id", "")
        value = cell.get("value", "")
        label = unescape(value).strip() if value else ""
        source = cell.get("source", "")
        target = cell.get("target", "")
        is_edge = cell.get("edge") == "1"

        if is_edge and source and target:
            edges.append((source, target, label))
        elif label and cell_id:
            nodes[cell_id] = label

    if nodes:
        parts.append("## Nodes\n")
        for node_id, label in nodes.items():
            parts.append(f"- **{label}** (id: {node_id})")

    if edges:
        parts.append("\n## Connections\n")
        for source, target, label in edges:
            src_label = nodes.get(source, source)
            tgt_label = nodes.get(target, target)
            arrow = f" —[{label}]→ " if label else " → "
            parts.append(f"- {src_label}{arrow}{tgt_label}")

    return "\n".join(parts)


@server.tool()
def read_svg(path: str) -> str:
    """Parse an SVG file and extract text elements and structure.

    Useful for architecture diagrams and flowcharts saved as SVG.

    Args:
        path: File path (relative to workspace root or absolute).
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"ERROR: file not found: {resolved}"

    try:
        tree = ET.parse(resolved)
    except ET.ParseError as exc:
        return f"ERROR: cannot parse SVG: {exc}"

    root = tree.getroot()

    texts: list[str] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "text":
            t = "".join(elem.itertext()).strip()
            if t:
                texts.append(t)
        elif tag == "title":
            t = (elem.text or "").strip()
            if t:
                texts.append(f"[title: {t}]")
        elif tag == "desc":
            t = (elem.text or "").strip()
            if t:
                texts.append(f"[description: {t}]")

    ns = {"svg": "http://www.w3.org/2000/svg"}
    title_elem = root.find("svg:title", ns) or root.find("title")
    title = (title_elem.text if title_elem is not None else None) or resolved.name

    parts = [f"# {title} (SVG diagram)\n"]
    if texts:
        parts.append("## Text elements\n")
        for t in texts:
            parts.append(f"- {t}")
    else:
        parts.append("(no text elements found — this SVG may be purely graphical)")

    return "\n".join(parts)


@server.tool()
def read_csv(
    path: str,
    max_rows: int = _MAX_CSV_ROWS,
    delimiter: str = ",",
) -> str:
    """Read a CSV file and return it as a markdown table.

    Args:
        path: File path (relative to workspace root or absolute).
        max_rows: Maximum data rows to include (default 100).
        delimiter: Column delimiter (default comma).
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"ERROR: file not found: {resolved}"

    try:
        with open(resolved, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows: list[list[str]] = []
            total = 0
            for row in reader:
                total += 1
                if total <= max_rows + 1:
                    rows.append([c.strip() for c in row])
    except Exception as exc:
        return f"ERROR: cannot read CSV: {exc}"

    if not rows:
        return f"# {resolved.name}\n\n(empty file)"

    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    result = f"# {resolved.name}\n\n{header}\n{separator}\n{body}"

    if total > max_rows + 1:
        result += _truncation_notice(total - 1, max_rows, "rows", "max_rows")
    return result


@server.tool()
def read_text(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a plain text file with line numbers.

    Works with any text-based format: .txt, .md, .yaml, .json, .xml, .html, etc.

    Args:
        path: File path (relative to workspace root or absolute).
        start_line: First line to include (1-indexed, default 1).
        end_line: Last line to include (inclusive). Defaults to start_line + 999.
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"ERROR: file not found: {resolved}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: cannot read file: {exc}"

    lines = content.splitlines()
    total = len(lines)
    start = max(0, start_line - 1)
    end = min(total, end_line or start + 1000)

    numbered = [f"{i + 1:>6}\t{lines[i]}" for i in range(start, end)]
    result = f"# {resolved.name} ({total} lines)\n\n" + "\n".join(numbered)

    if end < total and end_line is None:
        result += _truncation_notice(total, end - start, "lines", "start_line/end_line")
    return result


@server.tool()
def list_files(
    directory: str = ".",
    pattern: str = "*",
    recursive: bool = False,
) -> str:
    """List files in a directory, optionally filtered by glob pattern.

    Useful for discovering documents before reading them.

    Args:
        directory: Directory path (relative to workspace root or absolute).
        pattern: Glob pattern to filter files (default "*").
        recursive: Search subdirectories (default false).
    """
    resolved = _resolve_path(directory)
    if not resolved.exists():
        return f"ERROR: directory not found: {resolved}"
    if not resolved.is_dir():
        return f"ERROR: not a directory: {resolved}"

    files = sorted(resolved.rglob(pattern) if recursive else resolved.glob(pattern))

    if not files:
        return f"No files matching '{pattern}' in {resolved}"

    max_files = 200
    parts = [f"# Files in {resolved} (pattern: {pattern})\n"]
    for f in files[:max_files]:
        if f.is_file():
            size = f.stat().st_size
            rel = f.relative_to(resolved)
            if size > 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size}B"
            parts.append(f"- {rel} ({size_str})")

    if len(files) > max_files:
        parts.append(f"\n... and {len(files) - max_files} more files")
    return "\n".join(parts)


def run_server(workspace: Path | None = None) -> None:
    """Entry point for the CLI launcher and ``__main__``."""
    global _workspace_root  # noqa: PLW0603
    _workspace_root = workspace or Path(os.environ.get("SWARMKIT_WORKSPACE", ".")).resolve()
    server.run()
