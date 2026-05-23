# Vedanta Advisor

Conversational spiritual advisor grounded in Hindu religious texts. Provides life guidance with exact verse citations, multiple philosophical perspectives, and honest acknowledgment of gaps.

## Architecture

| Phase | Topology | Agent Pattern | Purpose |
|---|---|---|---|
| 1. Ingestion | `ingest-scriptures` | Multi-agent (parallel) | Fetch and normalize scripture texts into ChromaDB |
| 2. Graph Building | `build-wisdom-graph` | Single-agent | Create semantic wisdom graph in GBrain from ChromaDB |
| 3. Conversation | `advisor` | Single-agent | Answer life questions using wisdom graph |
| 4. Improvement | `session-analyst` | Single-agent | Review sessions, propose graph improvements |

**ChromaDB** = citation source (raw verses, translations, commentaries)
**GBrain** = reasoning source (wisdom blocks, semantic connections, life-situation tags)

## Setup

```bash
# 1. Fetch scripture datasets
./workspace/scripts/fetch-tier1-datasets.sh

# 2. Initialize GBrain
./workspace/scripts/init-gbrain.sh

# 3. Set environment variables
export VEDANTA_DATASETS_DIR=./knowledge/datasets
export VEDANTA_CHROMADB_DIR=./knowledge/chromadb
export VEDANTA_BRAIN_DIR=./knowledge/brain
export VEDANTA_NOTES_DIR=./knowledge/notes
export OPENROUTER_API_KEY=your-key

# 4. Ingest scriptures into ChromaDB
swarmkit run workspace ingest-scriptures

# 5. Build wisdom graph
swarmkit run workspace build-wisdom-graph

# 6. Start the advisor
swarmkit chat workspace advisor
```

## Data Sources

| Text | Source | Format |
|---|---|---|
| Bhagavad Gita (700 verses, 21+ translations) | vedicscriptures/bhagavad-gita | JSON |
| Mahabharata (18 parvas) | bhavykhatri/DharmicData | JSON |
| Valmiki Ramayana | AshuVj/Valmiki_Ramayan_Dataset | JSON |
| Bhagavata Purana | gita/Datasets | JSON |
| Chanakya Niti | gita/Datasets | JSON |

## Design

See [design/details/vedanta-advisor.md](../../design/details/vedanta-advisor.md) for the full architecture document.
