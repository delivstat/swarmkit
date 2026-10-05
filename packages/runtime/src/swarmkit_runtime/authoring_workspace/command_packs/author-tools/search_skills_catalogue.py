#!/usr/bin/env python3
"""Author tool: look up a bundle in the swarmkit-skills catalogue.

The authoring charter puts the swarmkit-skills catalogue right after the
check-workspace step in the discovery ladder for skills + MCP servers (see the
skill / mcp-server mode prompts in topologies/skill.yaml and
topologies/mcp-server.yaml). This tool makes that lookup programmatic: given a short
query, return the catalogue entries that look like a match so the author can suggest
``swarmkit skill add bundle:<id>`` instead of hand-rolling an llm_prompt skill or a
custom MCP server config.

The catalogue manifest is **inlined here** on purpose:

- It is a short, stable list (15 entries at the time of writing) that changes slowly.
- Making the author depend on a live network fetch would couple every authoring run
  to github.com availability; a stale local copy is strictly safer than that.
- The ``swarmkit skill add bundle:<id>`` command does its own live lookup when the
  user actually installs — this tool only needs enough signal for the author to say
  \"a bundle exists for this; here is the command\".

If a new bundle lands in the catalogue, update ``_CATALOGUE`` here and ship it with
the runtime.

Stdin JSON (matches skills/search-skills-catalogue.yaml inputs):

    {\"query\": \"gmail\"}

Stdout JSON:

    {
      \"query\": \"gmail\",
      \"matches\": [
        {\"id\": \"gmail\", \"summary\": \"read + draft Gmail (OAuth)\",
         \"install\": \"swarmkit skill add bundle:gmail\",
         \"requires_credentials\": [\"gmail\"]}
      ]
    }

Exit codes:
    0 — search ran (``matches`` may be empty)
    2 — invalid input (missing or empty query)
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn, TypedDict


class _Entry(TypedDict):
    id: str
    summary: str
    keywords: tuple[str, ...]
    requires_credentials: tuple[str, ...]


_CATALOGUE: tuple[_Entry, ...] = (
    {
        "id": "gmail",
        "summary": "read + draft Gmail (OAuth)",
        "keywords": ("gmail", "mail", "email", "inbox", "google"),
        "requires_credentials": ("gmail",),
    },
    {
        "id": "google-calendar",
        "summary": "read + propose Calendar events (OAuth)",
        "keywords": ("calendar", "event", "meeting", "schedule", "google"),
        "requires_credentials": ("google-calendar",),
    },
    {
        "id": "filesystem",
        "summary": "read/write local files",
        "keywords": ("file", "filesystem", "disk", "read", "write"),
        "requires_credentials": (),
    },
    {
        "id": "git",
        "summary": "repo status, log, diff, blame",
        "keywords": ("git", "repo", "commit", "diff", "blame"),
        "requires_credentials": (),
    },
    {
        "id": "memory",
        "summary": "durable key/value memory for an agent",
        "keywords": ("memory", "store", "remember", "state"),
        "requires_credentials": (),
    },
    {
        "id": "fetch",
        "summary": "HTTP fetch, URL -> markdown",
        "keywords": ("fetch", "http", "url", "web", "markdown"),
        "requires_credentials": (),
    },
    {
        "id": "chrome-devtools",
        "summary": "drive a Chrome instance for DOM / network inspection",
        "keywords": ("chrome", "browser", "devtools", "dom", "network"),
        "requires_credentials": (),
    },
    {
        "id": "playwright",
        "summary": "full browser automation",
        "keywords": ("playwright", "browser", "automation", "scrape"),
        "requires_credentials": (),
    },
    {
        "id": "markitdown",
        "summary": "PDF/DOCX/HTML -> markdown",
        "keywords": ("pdf", "docx", "html", "markdown", "document", "convert"),
        "requires_credentials": (),
    },
    {
        "id": "excel",
        "summary": "read / write / query xlsx",
        "keywords": ("excel", "xlsx", "spreadsheet", "sheet"),
        "requires_credentials": (),
    },
    {
        "id": "duckdb",
        "summary": "run SQL over local parquet/csv",
        "keywords": ("duckdb", "sql", "query", "parquet", "csv", "database"),
        "requires_credentials": (),
    },
    {
        "id": "context7",
        "summary": "up-to-date library docs lookup",
        "keywords": ("docs", "library", "api", "reference", "context7"),
        "requires_credentials": (),
    },
    {
        "id": "sequential-thinking",
        "summary": "long-horizon reasoning / planning",
        "keywords": ("thinking", "reasoning", "plan", "chain"),
        "requires_credentials": (),
    },
    {
        "id": "serena",
        "summary": "code-search / semantic grep over a repo",
        "keywords": ("code", "search", "grep", "serena", "semantic"),
        "requires_credentials": (),
    },
    {
        "id": "time",
        "summary": "timezone-aware now / shift / parse",
        "keywords": ("time", "timezone", "date", "clock"),
        "requires_credentials": (),
    },
)


def _die(code: int, error: str, detail: str = "") -> NoReturn:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def _score(entry: _Entry, needle: str) -> int:
    """Simple keyword overlap score. A hit on the id counts for more than a keyword
    hit; this is enough signal for \"is there a bundle for X?\" without pretending to
    be semantic search."""
    score = 0
    if needle == entry["id"]:
        score += 10
    elif needle in entry["id"]:
        score += 5
    for kw in entry["keywords"]:
        if needle == kw:
            score += 4
        elif needle in kw or kw in needle:
            score += 2
    return score


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _die(2, "invalid_json", str(exc))

    query = ""
    if isinstance(payload, dict):
        q = payload.get("query")
        if isinstance(q, str):
            query = q.strip().lower()
    if not query:
        _die(2, "empty_query", 'input must be {"query": "<non-empty string>"}')

    scored = [(e, _score(e, query)) for e in _CATALOGUE]
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda es: (-es[1], es[0]["id"]))

    matches = [
        {
            "id": e["id"],
            "summary": e["summary"],
            "install": f"swarmkit skill add bundle:{e['id']}",
            "requires_credentials": list(e["requires_credentials"]),
        }
        for e, _ in scored
    ]
    sys.stdout.write(json.dumps({"query": query, "matches": matches}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
