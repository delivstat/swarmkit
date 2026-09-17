# /// script
# dependencies = ["mcp>=1.0,<2"]
# ///
"""A knowledge-search MCP server: full-text search over knowledge/docs, no external services.

Indexes every .md / .txt file under KNOWLEDGE_DIR (default knowledge/docs) into an in-memory
SQLite FTS5 table at startup, one row per section (a heading and the text under it), and exposes
one tool. Swap the index for a vector store and the tool's contract does not change — see the
ChromaDB variant in the tutorial.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("knowledge-search")
_db = sqlite3.connect(":memory:")


def _sections(path: Path) -> list[tuple[str, str]]:
    """Split a markdown file on headings: [(heading, body), ...]."""
    heading, body, out = path.stem, [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            if body:
                out.append((heading, "\n".join(body).strip()))
            heading, body = line.lstrip("# ").strip(), []
        else:
            body.append(line)
    if body:
        out.append((heading, "\n".join(body).strip()))
    return [(h, b) for h, b in out if b]


def _index() -> int:
    root = Path(os.environ.get("KNOWLEDGE_DIR", "knowledge/docs"))
    _db.execute("CREATE VIRTUAL TABLE docs USING fts5(source, heading, body)")
    n = 0
    for path in sorted(root.rglob("*")):
        if path.suffix in (".md", ".txt"):
            for heading, body in _sections(path):
                _db.execute("INSERT INTO docs VALUES (?, ?, ?)", (str(path), heading, body))
                n += 1
    _db.commit()
    return n


@server.tool()
def search_knowledge(query: str, limit: int = 3) -> str:
    """Search the knowledge base. Returns the best-matching sections as JSON: source file,
    heading, text. Cite the source when you use one."""
    terms = " OR ".join(re.findall(r"[A-Za-z0-9]+", query))
    rows = _db.execute(
        "SELECT source, heading, body FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
        (terms, limit),
    ).fetchall()
    return json.dumps(
        [{"source": s, "heading": h, "text": b} for s, h, b in rows], indent=2, ensure_ascii=False
    )


if __name__ == "__main__":
    print(f"indexed {_index()} sections", file=__import__("sys").stderr)
    server.run()
