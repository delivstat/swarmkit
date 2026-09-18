# /// script
# dependencies = ["mcp>=1.0,<2", "chromadb>=1.0"]
# ///
"""The same knowledge-search tool over a vector index (ChromaDB, local, embedded).

Same contract as search_server.py — `search_knowledge(query, limit)` returning sections with
their source — so a topology can switch servers in workspace.yaml and nothing else changes. The
index is built at startup from KNOWLEDGE_DIR and kept under CHROMADB_PATH; ChromaDB's default
embedding model (all-MiniLM-L6-v2, ~80 MB) is downloaded once.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import chromadb
from mcp.server.fastmcp import FastMCP

server = FastMCP("knowledge-search")
_client = chromadb.PersistentClient(path=os.environ.get("CHROMADB_PATH", "knowledge/chromadb"))
_collection = _client.get_or_create_collection("handbook")


def _sections(path: Path) -> list[tuple[str, str]]:
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
    ids, docs, metas = [], [], []
    for path in sorted(root.rglob("*")):
        if path.suffix in (".md", ".txt"):
            for i, (heading, body) in enumerate(_sections(path)):
                ids.append(f"{path}#{i}")
                docs.append(f"{heading}\n{body}")
                metas.append({"source": str(path), "heading": heading})
    if ids:
        _collection.upsert(ids=ids, documents=docs, metadatas=metas)
    return len(ids)


@server.tool()
def search_knowledge(query: str, limit: int = 3) -> str:
    """Search the knowledge base by meaning. Returns the closest sections as JSON: source file,
    heading, text. Cite the source when you use one."""
    res = _collection.query(query_texts=[query], n_results=limit)
    out = [
        {"source": m["source"], "heading": m["heading"], "text": d.split("\n", 1)[1]}
        for d, m in zip(res["documents"][0], res["metadatas"][0], strict=True)
    ]
    return json.dumps(out, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    print(f"indexed {_index()} sections", file=sys.stderr)
    server.run()
