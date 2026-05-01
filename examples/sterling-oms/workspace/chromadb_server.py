# /// script
# dependencies = ["mcp[cli]>=1.0", "chromadb>=1.0", "sentence-transformers[onnx]>=3.0"]
# ///
"""ChromaDB semantic search MCP server — queries pre-built vector index.

Lightweight query-only server. The heavy embedding work is done at
ingestion time by ingest-docs.py. At query time, this server embeds
the query string (~50ms) and does a cosine similarity search.

Usage:
    export CHROMADB_PATH=~/sterling-knowledge/product-docs/chromadb
    uv run chromadb_server.py
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("chromadb-search")

_DB_PATH = os.environ.get("CHROMADB_PATH", "chromadb")
_model = None


def _get_model():
    global _model  # noqa: PLW0603
    if _model is None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        _model = SentenceTransformer("all-MiniLM-L6-v2", backend="onnx")
    return _model


def _get_collection():
    import chromadb  # noqa: PLC0415

    client = chromadb.PersistentClient(path=_DB_PATH)
    collections = client.list_collections()
    if not collections:
        return None
    return client.get_collection(collections[0].name)


@server.tool()
def search_docs(query: str, n_results: int = 5) -> str:
    """Semantic search over documentation. Finds conceptually related content."""
    collection = _get_collection()
    if collection is None:
        return "No ChromaDB collection found. Run ingest-docs.py first."

    model = _get_model()
    embedding = model.encode([query], normalize_embeddings=True).tolist()

    results = collection.query(
        query_embeddings=embedding,
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return f"No results for '{query}'."

    lines = [f"Found {len(results['documents'][0])} results:\n"]
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
        strict=False,
    ):
        source = meta.get("source", "unknown")
        score = 1 - dist
        snippet = doc[:300] + "..." if len(doc) > 300 else doc
        lines.append(f"**{meta.get('file', '')}** ({source}) [score: {score:.2f}]")
        lines.append(f"  {snippet}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    server.run()
