# Level 10: Knowledge & RAG

Give your agents access to a knowledge base — search documents, retrieve facts, and ground responses in real data instead of model hallucinations.

## What you'll learn

- SwarmKit's built-in knowledge MCP server
- Document reader MCP server
- ChromaDB for vector search
- GBrain for knowledge graphs
- Building an ingestion pipeline
- Grounding verification

## The knowledge stack

SwarmKit supports multiple knowledge backends. Use them together or separately:

| Backend | Best for | Setup |
|---------|----------|-------|
| **Knowledge MCP** | Workspace docs, schema queries | Built-in, zero config |
| **Document Reader** | PDF, DOCX, Excel, CSV parsing | Built-in, zero config |
| **ChromaDB** | Large document collections, vector search | Custom MCP server |
| **GBrain** | Knowledge graphs, semantic search, relationships | Install gbrain CLI |

## Build it

### 1. Knowledge MCP server (built-in)

SwarmKit ships a knowledge MCP server that can search workspace files and schemas:

```bash
# Start the knowledge server
swarmkit knowledge-server --repo .
```

Or add it as an MCP server in your workspace:

```yaml
mcp_servers:
  - id: knowledge
    transport: stdio
    command: ["swarmkit", "knowledge-server", "--repo", "."]
```

Tools available:
- `search_codebase` — search workspace files by content
- `read_workspace_file` — read a specific file
- `get_schema` — get SwarmKit JSON schemas
- `validate_workspace` — validate the workspace

### 2. Document reader (built-in)

Read PDFs, Word docs, Excel, CSV, and more:

```bash
swarmkit docs-reader
```

```yaml
mcp_servers:
  - id: docs-reader
    transport: stdio
    command: ["swarmkit", "docs-reader"]
```

Tools: `read_pdf`, `read_docx`, `read_excel`, `read_csv`, `view_image`, `list_files`.

### 3. ChromaDB for vector search

Build a custom MCP server that searches a ChromaDB collection:

```python
# servers/search_server.py
"""Knowledge search MCP server backed by ChromaDB."""
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
import asyncio
import json
import os

server = Server("knowledge-search")

def get_collection():
    import chromadb
    client = chromadb.PersistentClient(
        path=os.environ.get("CHROMADB_PATH", "./knowledge/chromadb")
    )
    return client.get_or_create_collection("documents")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="search_knowledge",
            description="Search the knowledge base for relevant documents.",
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {"type": "integer", "description": "Max results.", "default": 5},
                },
            },
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search_knowledge":
        collection = get_collection()
        results = collection.query(
            query_texts=[arguments["query"]],
            n_results=arguments.get("limit", 5),
        )
        docs = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            docs.append({"content": doc[:500], "metadata": meta})
        return [TextContent(type="text", text=json.dumps(docs, indent=2))]
    return [TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

Register and create the skill:

```yaml
# workspace.yaml — add server
mcp_servers:
  - id: knowledge-search
    transport: stdio
    command: ["uv", "run", "servers/search_server.py"]
    env:
      CHROMADB_PATH: "${CHROMADB_PATH}"
```

```yaml
# skills/search-knowledge.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: search-knowledge
  name: Search Knowledge
  description: Search the knowledge base for relevant documents.
category: capability
implementation:
  type: mcp_tool
  server: knowledge-search
  tool: search_knowledge
provenance:
  authored_by: human
  version: 1.0.0
```

### 4. Ingest documents

Create a simple ingestion script:

```python
# scripts/ingest.py
"""Ingest documents into ChromaDB."""
import chromadb
import os
from pathlib import Path

client = chromadb.PersistentClient(path="./knowledge/chromadb")
collection = client.get_or_create_collection("documents")

docs_dir = Path("./knowledge/docs")
for filepath in docs_dir.glob("**/*.txt"):
    content = filepath.read_text()
    collection.add(
        documents=[content],
        metadatas=[{"source": str(filepath), "type": "text"}],
        ids=[str(filepath)],
    )
    print(f"  Ingested: {filepath}")

print(f"Total documents: {collection.count()}")
```

```bash
mkdir -p knowledge/docs
echo "The Bhagavad Gita teaches about dharma and duty." > knowledge/docs/sample.txt
uv run scripts/ingest.py
```

### 5. GBrain for knowledge graphs

GBrain provides a knowledge graph with semantic search, graph edges, and fact extraction:

```bash
# Install GBrain
bun install -g github:garrytan/gbrain

# Initialize
gbrain init --pglite --embedding-model zeroentropyai:zembed-1 --embedding-dimensions 1280

# Add a page
echo "# Karma
Karma means action and its consequences." | gbrain put karma

# Search
gbrain search "what is karma"
```

Add to workspace:

```yaml
mcp_servers:
  - id: gbrain
    transport: stdio
    command: ["gbrain", "serve"]
```

```yaml
# skills/brain-search.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: brain-search
  name: Brain Search
  description: Search the GBrain knowledge graph.
category: capability
implementation:
  type: mcp_tool
  server: gbrain
  tool: search
provenance:
  authored_by: human
  version: 1.0.0
```

### 6. Grounding verification

Add a decision skill that checks if the agent's response is grounded in the knowledge base (not hallucinated):

```yaml
# skills/grounding-check.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: grounding-check
  name: Grounding Check
  description: Verify that the response is grounded in retrieved knowledge.
category: decision
implementation:
  type: llm_prompt
  prompt: |
    Check if this response is grounded in the provided context.
    A grounded response only makes claims supported by the
    retrieved documents. An ungrounded response invents facts
    or cites non-existent sources.

    RESPONSE: {{input}}

    Return: {"verdict": "pass" or "fail", "reasoning": "..."}
output_schema:
  type: object
  required: [verdict, reasoning]
  properties:
    verdict:
      type: string
      enum: [pass, fail]
    reasoning:
      type: string
provenance:
  authored_by: human
  version: 1.0.0
```

Bind as a post_output gate:

```yaml
governance:
  decision_skills:
    - id: grounding-check
      trigger: post_output
      scope: "*"
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── knowledge/
│   ├── chromadb/           # vector store
│   └── docs/               # source documents
├── servers/
│   └── search_server.py    # ChromaDB MCP server
├── scripts/
│   └── ingest.py           # document ingestion
├── archetypes/
├── skills/
│   ├── search-knowledge.yaml
│   ├── brain-search.yaml
│   └── grounding-check.yaml
├── gates/
└── topologies/
```

## Next

[Level 11: Serve & HTTP API](11-serve-api.md) — run your workspace as a persistent HTTP service.
