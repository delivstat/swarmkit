# /// script
# dependencies = ["mcp[cli]>=1.0", "graphifyy"]
# ///
"""Graphify MCP server — wraps graphify CLI for code knowledge graph queries.

Exposes graphify's query, path, and explain commands as MCP tools.
Requires a pre-built graph: cd $PROJECT_CODE && graphify

Usage:
    export STERLING_CODE_GRAPH=~/sterling-project-code/graphify-out/graph.json
    uv run graphify_server.py
"""

from __future__ import annotations

import os
import subprocess

from mcp.server.fastmcp import FastMCP

server = FastMCP("code-graph")

_GRAPH = os.environ.get(
    "STERLING_CODE_GRAPH",
    "graphify-out/graph.json",
)
_CODE_DIR = os.environ.get("STERLING_PROJECT_CODE_DIR", ".")


def _run(args: list[str]) -> str:
    result = subprocess.run(
        ["graphify", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        err = result.stderr.strip()
        return f"Error: {err}" if err else "Command failed."
    return output or "No results."


@server.tool()
def query_code_graph(question: str, budget: int = 2000) -> str:
    """Query the code knowledge graph — ~2K tokens vs ~50K for raw files."""
    return _run(["query", question, "--graph", _GRAPH, "--budget", str(budget)])


@server.tool()
def explain_code_entity(entity: str) -> str:
    """Explain a code entity and its neighbors — classes, methods, call chains, dependencies."""
    return _run(["explain", entity, "--graph", _GRAPH])


@server.tool()
def find_code_path(source: str, target: str) -> str:
    """Find the shortest path between two code entities — how class A reaches class B."""
    return _run(["path", source, target, "--graph", _GRAPH])


@server.tool()
def grep_project_code(pattern: str, file_glob: str = "*.java", max_results: int = 20) -> str:
    """Search file contents for a text pattern (grep). Returns matching lines with relative paths."""
    result = subprocess.run(
        ["grep", "-rn", "--include", file_glob, "-i", pattern, "."],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=_CODE_DIR,
    )
    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    if not lines:
        return f"No matches for '{pattern}' in {file_glob} files."
    if len(lines) > max_results:
        lines = lines[:max_results]
        total = len(result.stdout.strip().split(chr(10)))
        lines.append(f"... ({total} total matches, showing first {max_results})")
    return "\n".join(lines)


if __name__ == "__main__":
    server.run()
