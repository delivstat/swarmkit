#!/usr/bin/env python3
"""Keyword search over SwarmKit's `llms-full.txt`.

Reads {query, max_results?} on stdin, returns {results: [{heading, excerpt}]}. The
retrieval is intentionally simple for v1 — exact + case-insensitive token match against
section headings and body paragraphs. Swap in a vector store later without changing the
skill shape; the Dot Author depends on the output contract, not the retrieval method.

Doc source:
    AUTHOR_LLMS_FULL_PATH    default: ./llms-full.txt (resolved from CWD)

Exit codes:
    0 — results returned (even if empty)
    2 — invalid request
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SECTION_RE = re.compile(r"^(#{1,4})\s+(.+)$")


def _load_doc() -> str | None:
    path = Path(os.environ.get("AUTHOR_LLMS_FULL_PATH", "llms-full.txt"))
    if not path.is_file():
        # Try repo root fallback — handy when the script runs under swarmkit serve where
        # CWD is the workspace but the doc lives beside the runtime install.
        for candidate in (Path.cwd() / "llms-full.txt", Path("/app/llms-full.txt")):
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        return None
    return path.read_text(encoding="utf-8")


def _split_sections(doc: str) -> list[tuple[str, str]]:
    """Return [(heading, body)] pairs where heading is the markdown header and body
    is the text until the next header. An untitled preamble gets a synthetic heading."""
    out: list[tuple[str, str]] = []
    current_heading = "(preamble)"
    buf: list[str] = []
    for line in doc.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if buf:
                out.append((current_heading, "\n".join(buf).strip()))
            current_heading = m.group(2).strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        out.append((current_heading, "\n".join(buf).strip()))
    return out


def _score(query_tokens: list[str], text: str) -> int:
    text_lower = text.lower()
    return sum(text_lower.count(tok) for tok in query_tokens)


def _die(code: int, error: str, detail: str = "") -> None:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def main() -> int:
    raw = sys.stdin.read()
    try:
        req: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(2, "invalid_json", str(exc))

    query = str(req.get("query") or "").strip()
    if not query:
        _die(2, "missing_field", "query")
    max_results = int(req.get("max_results") or 3)
    max_results = max(1, min(10, max_results))

    doc = _load_doc()
    if doc is None:
        sys.stdout.write(json.dumps({"results": [], "note": "llms-full.txt not found"}) + "\n")
        return 0

    query_tokens = [t for t in re.split(r"\s+", query.lower()) if t]
    sections = _split_sections(doc)
    scored = sorted(
        (
            (score, heading, body)
            for (heading, body) in sections
            if (score := _score(query_tokens, heading + "\n" + body)) > 0
        ),
        key=lambda t: -t[0],
    )
    results = [
        {
            "heading": heading,
            # Cap each excerpt so the agent's context stays bounded; whole sections can be
            # very long. The agent can re-query with a tighter term if it needs more.
            "excerpt": body[:1200] + ("…" if len(body) > 1200 else ""),
        }
        for (_score_val, heading, body) in scored[:max_results]
    ]
    sys.stdout.write(json.dumps({"results": results}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
