# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "mcp>=1.0"]
# ///
"""Scripture Search MCP Server — ChromaDB verse lookup for Vedanta Advisor.

Citation source only. The conversational agent queries GBrain for meaning;
this server provides exact verse text, translations, and commentaries
when the agent needs to cite a specific reference.

Tools:
  - store_verse: ingest a verse into a text-family collection
  - get_verse: exact lookup by verse ID (e.g. gita:2:47)
  - search_verses: keyword/semantic search across collections
  - list_collections: list available text families
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import chromadb
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print(
        "Missing dependencies. Install with:\n"
        "  pip install chromadb mcp",
        file=sys.stderr,
    )
    sys.exit(1)

CHROMADB_PATH = os.environ.get("CHROMADB_PATH", "./knowledge/chromadb")

COLLECTIONS = [
    "gita",
    "upanishads",
    "mahabharata",
    "mahabharata_english",
    "ramayana",
    "puranas",
    "niti",
    "vedanta-texts",
    "shastras",
    "devotional",
    "buddhist",
    "wisdom-stories",
    "ethics",
    "vedas",
]

server = Server("scripture-search")
_client: chromadb.ClientAPI | None = None


def _get_client() -> chromadb.ClientAPI:
    global _client  # noqa: PLW0603
    if _client is None:
        path = Path(CHROMADB_PATH)
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
    return _client


def _get_or_create_collection(name: str) -> chromadb.Collection:
    client = _get_client()
    return client.get_or_create_collection(name=name)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="store_verse",
            description=(
                "Store a scripture verse in ChromaDB. Provide the verse data "
                "as a JSON object with: id, text_family, book, chapter, verse, "
                "sanskrit, transliteration, translations, commentaries, context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "verse_data": {
                        "type": "object",
                        "description": "Verse data in common verse schema",
                    },
                },
                "required": ["verse_data"],
            },
        ),
        Tool(
            name="get_verse",
            description=(
                "Get a specific verse by ID (e.g. 'gita:2:47'). Use 'detail' "
                "to control response size: 'quote' (~500B: Sanskrit + one "
                "translation), 'summary' (~2KB: Sanskrit + 3 key commentators), "
                "or 'full' (~15-20KB: all 21 commentators). Default: summary. "
                "Use 'quote' when you already know the teaching from GBrain "
                "and just need the exact text for citation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "verse_id": {
                        "type": "string",
                        "description": "Verse ID (e.g. 'gita:2:47', 'isha:1')",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["quote", "summary", "full"],
                        "description": "Level of detail: quote (~500B), summary (~2KB), full (~15KB). Default: summary.",
                    },
                },
                "required": ["verse_id"],
            },
        ),
        Tool(
            name="search_verses",
            description=(
                "Search scripture verses by keyword or theme. Optionally "
                "filter by text family (gita, upanishads, mahabharata, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keyword or theme)",
                    },
                    "text_family": {
                        "type": "string",
                        "description": "Filter by text family (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_collections",
            description="List available scripture collections and their verse counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "store_verse":
        return _store_verse(arguments.get("verse_data", {}))
    elif name == "get_verse":
        return _get_verse(arguments.get("verse_id", ""), arguments.get("detail", "summary"))
    elif name == "search_verses":
        return _search_verses(
            arguments.get("query", ""),
            arguments.get("text_family"),
            arguments.get("limit", 10),
        )
    elif name == "list_collections":
        return _list_collections()
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _store_verse(verse_data: dict) -> list[TextContent]:
    verse_id = verse_data.get("id", "")
    text_family = verse_data.get("text_family", "")
    if not verse_id or not text_family:
        return [TextContent(type="text", text="Error: verse_data must have 'id' and 'text_family'")]

    collection = _get_or_create_collection(text_family)

    doc_text = _build_document_text(verse_data)
    metadata = {
        "verse_id": verse_id,
        "text_family": text_family,
        "book": verse_data.get("book", ""),
        "chapter": str(verse_data.get("chapter", "")),
        "verse": str(verse_data.get("verse", "")),
    }

    collection.upsert(
        ids=[verse_id],
        documents=[doc_text],
        metadatas=[metadata],
    )

    return [TextContent(type="text", text=f"Stored verse {verse_id} in {text_family}")]


KEY_COMMENTATOR_NAMES = {
    "Shankaracharya", "Ramanujacharya", "Swami Gambirananda",
    "Swami Sivananda", "A.C. Bhaktivedanta Swami Prabhupada",
}


def _get_verse(verse_id: str, detail: str = "summary") -> list[TextContent]:
    parts = verse_id.split(":")
    if not parts:
        return [TextContent(type="text", text=f"Invalid verse ID: {verse_id}")]

    text_family = parts[0]
    try:
        collection = _get_client().get_collection(text_family)
    except Exception:
        return [TextContent(type="text", text=f"Collection '{text_family}' not found")]

    results = collection.get(ids=[verse_id], include=["documents", "metadatas"])
    if not results["ids"]:
        return [TextContent(type="text", text=f"Verse '{verse_id}' not found")]

    full_text = results["documents"][0]

    if detail == "full":
        return [TextContent(type="text", text=full_text)]

    lines = full_text.split("\n")

    if detail == "quote":
        quote_lines = []
        has_translation = False
        for line in lines:
            if line.startswith(("Sanskrit:", "Transliteration:", "Speaker:")):
                quote_lines.append(line)
            elif line.startswith("Translation [") and not has_translation:
                quote_lines.append(line)
                has_translation = True
        return [TextContent(type="text", text="\n".join(quote_lines))]

    summary_lines = []
    for line in lines:
        if line.startswith(("Sanskrit:", "Transliteration:", "Speaker:",
                            "Context:", "Listener:", "--- ")):
            summary_lines.append(line)
        elif line.startswith(("Translation [", "Commentary [")):
            if any(name in line for name in KEY_COMMENTATOR_NAMES):
                summary_lines.append(line)

    return [TextContent(type="text", text="\n".join(summary_lines))]


def _search_verses(query: str, text_family: str | None, limit: int) -> list[TextContent]:
    if text_family:
        collections_to_search = [text_family]
    else:
        collections_to_search = COLLECTIONS

    all_results = []
    for coll_name in collections_to_search:
        try:
            collection = _get_client().get_collection(coll_name)
        except Exception:
            continue

        results = collection.query(
            query_texts=[query],
            n_results=min(limit, 5),
            include=["documents", "metadatas", "distances"],
        )

        for i, doc_id in enumerate(results["ids"][0]):
            all_results.append({
                "id": doc_id,
                "collection": coll_name,
                "distance": results["distances"][0][i] if results["distances"] else None,
                "text": results["documents"][0][i][:500],
            })

    all_results.sort(key=lambda x: x.get("distance") or 999)
    all_results = all_results[:limit]

    if not all_results:
        return [TextContent(type="text", text=f"No verses found for: {query}")]

    output = json.dumps(all_results, indent=2, ensure_ascii=False)
    return [TextContent(type="text", text=output)]


def _list_collections() -> list[TextContent]:
    client = _get_client()
    result = []
    for name in COLLECTIONS:
        try:
            coll = client.get_collection(name)
            result.append({"collection": name, "count": coll.count()})
        except Exception:
            result.append({"collection": name, "count": 0, "status": "not created"})

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def _build_document_text(verse_data: dict) -> str:
    parts = []

    sanskrit = verse_data.get("sanskrit", "")
    if sanskrit:
        parts.append(f"Sanskrit: {sanskrit}")

    transliteration = verse_data.get("transliteration", "")
    if transliteration:
        parts.append(f"Transliteration: {transliteration}")

    translations = verse_data.get("translations", [])
    for t in translations:
        author = t.get("author", "Unknown")
        tradition = t.get("tradition", "")
        text = t.get("text", "")
        label = f"{author} ({tradition})" if tradition else author
        parts.append(f"Translation [{label}]: {text}")

    commentaries = verse_data.get("commentaries", [])
    for c in commentaries:
        author = c.get("author", "Unknown")
        tradition = c.get("tradition", "")
        text = c.get("text", "")
        label = f"{author} ({tradition})" if tradition else author
        parts.append(f"Commentary [{label}]: {text}")

    context = verse_data.get("context", {})
    if context:
        speaker = context.get("speaker", "")
        listener = context.get("listener", "")
        narrative = context.get("narrative_context", "")
        if speaker:
            parts.append(f"Speaker: {speaker}")
        if listener:
            parts.append(f"Listener: {listener}")
        if narrative:
            parts.append(f"Context: {narrative}")

    return "\n".join(parts)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
