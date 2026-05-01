# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Full-text search MCP server — SQLite FTS5 over ingested documentation.

Fast exact keyword search. Use for precise queries like API names,
table names, config names. Falls back to the semantic search (ChromaDB)
for discovery queries where exact terms aren't known.

Usage:
    export FTS_DB_PATH=~/sterling-knowledge/product-docs/fts.db
    uv run fts_server.py
"""

from __future__ import annotations

import os
import sqlite3

from mcp.server.fastmcp import FastMCP

server = FastMCP("fts-search")

_DB_PATH = os.environ.get("FTS_DB_PATH", "fts.db")


def _get_conn() -> sqlite3.Connection:
    return sqlite3.connect(_DB_PATH)


@server.tool()
def search_text(query: str, limit: int = 10) -> str:
    """Full-text search over documentation. Fast exact keyword matching with ranking."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT source, file_name, snippet(docs_fts, 3, '>>>', '<<<', '...', 64), "
            "       rank "
            "FROM docs_fts "
            "WHERE docs_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (query, limit),
        ).fetchall()
    except Exception as exc:
        return f"Search error: {exc}"
    finally:
        conn.close()

    if not rows:
        return f"No results for '{query}'. Try different keywords."

    lines = [f"Found {len(rows)} results:\n"]
    for source, file_name, snippet, _rank in rows:
        lines.append(f"**{file_name}** ({source})")
        lines.append(f"  {snippet}")
        lines.append("")
    return "\n".join(lines)


@server.tool()
def search_text_by_file(query: str, file_pattern: str, limit: int = 10) -> str:
    """Search within files matching a pattern (e.g. 'order' for order-related files)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT source, file_name, snippet(docs_fts, 3, '>>>', '<<<', '...', 64) "
            "FROM docs_fts "
            "WHERE docs_fts MATCH ? AND file_name LIKE ? "
            "ORDER BY rank "
            "LIMIT ?",
            (query, f"%{file_pattern}%", limit),
        ).fetchall()
    except Exception as exc:
        return f"Search error: {exc}"
    finally:
        conn.close()

    if not rows:
        return f"No results for '{query}' in files matching '{file_pattern}'."

    lines = [f"Found {len(rows)} results:\n"]
    for source, file_name, snippet in rows:
        lines.append(f"**{file_name}** ({source})")
        lines.append(f"  {snippet}")
        lines.append("")
    return "\n".join(lines)


@server.tool()
def get_full_chunk(chunk_id: str) -> str:
    """Retrieve the full text of a specific chunk by ID (from search results)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT source, content FROM docs_fts WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        conn.close()

    if not row:
        return f"Chunk '{chunk_id}' not found."
    return f"Source: {row[0]}\n\n{row[1]}"


if __name__ == "__main__":
    server.run()
