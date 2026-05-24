# Vedanta Advisor

A conversational spiritual advisor grounded in Hindu religious texts. Ask a life question, get wisdom from the Bhagavad Gita, Upanishads, Mahabharata, Ramayana, and more — with exact verse citations, multiple philosophical perspectives, and stories that bring the teachings alive.

Not a chatbot with generic answers. A knowledge system that traces every recommendation to a specific shloka, commentary, or narrative episode. Honest when it doesn't have an answer.

---

## What It Does

Three conversation modes:

**Life Guidance** — "I keep putting off starting my business because what if it fails"
→ Searches the wisdom graph for teachings on fear, detachment, and action. Returns Gita 2.47 with Arjuna's story, Shankaracharya's commentary, and a practical reflection.

**Stories** — "Tell me the story of Rama and Shabari" or "What happened to Sita after the war"
→ Searches knowledge blocks for narrative episodes. Tells the story, shares its significance, connects to related stories and teachings.

**Study** — "What's the difference between Advaita and Vishishtadvaita" or "Explain the Mandukya Upanishad"
→ Searches both wisdom and knowledge blocks. Explains concepts with verse references, multiple darshana perspectives, and historical context.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | System or pyenv |
| uv | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 18+ | `nvm install 18` or system |
| Bun | 1.3+ | `curl -fsSL https://bun.sh/install \| bash` |
| GBrain | 0.40+ | `bun install -g github:garrytan/gbrain` |
| SwarmKit | 1.2.49+ | `uv tool install swarmkit-runtime` |

**API Keys** (at least one required):

| Key | Used For | Get It |
|---|---|---|
| `OPENROUTER_API_KEY` | LLM calls for agents (required) | openrouter.ai |
| GBrain expansion model uses OpenRouter by default — no separate key needed |

---

## Quick Start

```bash
cd examples/vedanta-advisor

# 1. Fetch scripture datasets from GitHub (~5 min)
bash workspace/scripts/fetch-tier1-datasets.sh

# 2. Ingest into ChromaDB (~10 min, 98K+ documents)
uv run workspace/scripts/ingest-to-chromadb.py

# 3. Initialize GBrain knowledge graph
bash workspace/scripts/init-gbrain.sh

# 4. Set environment variables
export OPENROUTER_API_KEY=your-key
export VEDANTA_CHROMADB_DIR=./knowledge/chromadb
export VEDANTA_NOTES_DIR=./knowledge/notes

# 5. Build the wisdom graph (run once, takes time)
swarmkit run workspace build-wisdom-graph

# 6. Talk to the advisor
swarmkit chat workspace advisor
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  USER CONVERSATION                   │
│                                                      │
│  "I'm struggling with a decision at work..."        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              WISDOM ADVISOR AGENT                    │
│                                                      │
│  1. Detects mode (life guidance / story / study)    │
│  2. Searches GBrain for relevant wisdom/knowledge   │
│  3. Traverses graph edges for related teachings     │
│  4. Pulls exact verses from ChromaDB for citation   │
│  5. Weaves teaching + story + verse into response   │
└───────────┬─────────────────────┬───────────────────┘
            │                     │
            ▼                     ▼
┌───────────────────┐  ┌─────────────────────────────┐
│     GBrain        │  │        ChromaDB              │
│  (Reasoning)      │  │      (Citation)              │
│                   │  │                               │
│  Wisdom Blocks    │  │  98,263 verses               │
│  Knowledge Blocks │  │  21 commentators (Gita)      │
│  Graph Edges      │  │  Sanskrit + translations     │
│  Life situations  │  │  4 collections               │
│  Emotional states │  │                               │
│  Cross-text links │  │  gita: 719                   │
│                   │  │  ramayana: 23,402            │
│  Hybrid search:   │  │  mahabharata: 73,817         │
│  keyword + graph  │  │  niti: 321                   │
└───────────────────┘  └─────────────────────────────┘
```

### Two-Layer Storage

**ChromaDB is the citation source.** Raw verses, translations, commentaries stored per text family. Queried by exact verse reference (`gita:2:47`), not by semantic similarity. No RAG at conversation time — the agent never does "find me something relevant" against raw text.

**GBrain is the reasoning source.** Pre-processed wisdom blocks and knowledge blocks with life-situation tags, emotional states, cross-text connections, and multiple darshana perspectives. The conversational agent searches this for meaning. All semantic reasoning happens against the graph, not against raw scripture text.

Why this split? Because embedding similarity fails for spiritual advice. "I'm afraid of failing" and "कर्मण्येवाधिकारस्ते" have zero embedding similarity but are deeply connected. The graph builder agent has already made that connection and stored it as a tagged wisdom block. The advisor just looks it up.

### Data Flow

```
Phase 1: INGESTION (one-time, parallel)
  GitHub repos → fetch-tier1-datasets.sh → raw JSON files
  Raw JSON → ingest-to-chromadb.py → ChromaDB collections

Phase 2: GRAPH BUILDING (one-time, single agent)
  ChromaDB → graph builder agent reads all verses
  Agent interprets, tags, connects → writes wisdom/knowledge blocks to GBrain
  GBrain auto-wires entity references (verse IDs, character names)
  Agent adds semantic edges (complements, tensions_with, illustrated_by)

Phase 3: CONVERSATION (ongoing, single agent)
  User question → GBrain hybrid search (situation/emotion/theme)
  GBrain returns matching wisdom/knowledge blocks with edges
  Agent reasons over blocks, selects depth level
  Agent needs exact verse → ChromaDB lookup (citation only)
  Agent → response with teaching + story + verse citation

Phase 4: IMPROVEMENT (periodic, single agent)
  Session logs → analyst agent reviews conversations
  Identifies: gaps, ineffective answers, missing connections
  Proposes improvements → human review → graph updates
```

---

## The Four Phases

### Phase 1: Ingestion

Parallel agents ingest scripture texts from GitHub datasets into ChromaDB. Each text family is ingested independently — genuinely parallel, no cross-source dependencies.

| Collection | Documents | Source |
|---|---|---|
| `gita` | 719 verses × 21 commentators | vedicscriptures/bhagavad-gita |
| `ramayana` | 23,402 shlokas | AshuVj/Valmiki_Ramayan_Dataset |
| `mahabharata` | 73,817 shlokas (18 parvas) | bhavykhatri/DharmicData |
| `niti` | 321 verses | gita/Datasets (Chanakya Niti) |

Each verse is stored with: Sanskrit original, transliteration, all available translations (with author and tradition), all available commentaries, speaker/listener context, and narrative context.

The Bhagavad Gita dataset is the richest — 21 commentators including Shankaracharya (Advaita), Ramanujacharya (Vishishtadvaita), Madhvacharya (Dvaita), Prabhupada (Gaudiya Vaishnavism), and more.

**Run ingestion:**
```bash
# Fetch datasets (clones 5 GitHub repos)
bash workspace/scripts/fetch-tier1-datasets.sh

# Ingest into ChromaDB (takes ~10 minutes for 98K documents)
uv run workspace/scripts/ingest-to-chromadb.py
```

### Phase 2: Graph Building

A single agent reads all ChromaDB collections and builds the semantic wisdom graph in GBrain. This must be single-agent because connecting verses across texts requires one context window — a Gita verse on detachment connects to an Upanishadic verse on renunciation connects to Arjuna's story in the Mahabharata. That chain only works if one agent sees all three.

The agent creates two types of GBrain pages:

**Wisdom Blocks** — indexed by life situation:
```yaml
title: detachment-from-outcome
type: wisdom
tags: [detachment, action, karma-yoga]

Core Teaching: Your right is to action alone, never to its fruits.

Darshana Perspectives:
  Karma Yoga: Act with full effort but release attachment to results.
  Advaita: Attachment reinforces the ego's illusion of control.
  Bhakti: Offer every action to God. Act as an instrument.

Applicable When:
  - procrastinating due to fear of failure
  - obsessing over results at work
  - starting a new venture

Teaching Story:
  Arjuna stood between two armies, his own family on both sides,
  and dropped his bow. Not because he was afraid of losing — because
  he was overwhelmed by the consequences. Krishna's response: your
  worry about consequences is the actual problem...

Source Verses:
  - gita:2:47 (primary)
  - gita:3:19 (supporting)
  - isha:1 (cross-text)
```

**Knowledge Blocks** — indexed by character, event, concept:
```yaml
title: rama-and-shabari
type: knowledge
tags: [rama, shabari, devotion, aranya-kanda]

Summary: An elderly tribal woman waits her entire life for Rama...

The Story: [full narrative told as a story, not an encyclopedia entry]

Characters:
  - Rama: prince in exile, embodiment of dharma
  - Shabari: elderly devotee, tribal woman

Significance: Devotion transcends caste, gender, social status...

Common Questions:
  - Why did Rama eat Shabari's half-eaten berries?
  - What does Shabari represent in Hindu philosophy?
```

**Graph edges** connect blocks semantically:

| Edge Type | Meaning |
|---|---|
| `complements` | Teachings that reinforce each other |
| `tensions_with` | Apparent contradictions (with resolution) |
| `deepened_by` | More advanced teaching extending this one |
| `illustrated_by` | Story that demonstrates a teaching |
| `teaches` | Knowledge block → wisdom block it embodies |
| `continues` | Narrative sequence (what happens next) |
| `character_in` | Character → stories they appear in |
| `same_verse_different_lens` | Same verse, different darshana |

**Run graph building:**
```bash
swarmkit run workspace build-wisdom-graph
```

### Phase 3: Conversation

A single conversational agent with access to GBrain and ChromaDB. Single-agent because conversational context builds across turns — the agent needs to remember that 3 turns ago it recommended Gita 2.47 and now needs to address the counter-teaching.

The agent detects three modes from the user's question:

| User Says | Mode | Searches |
|---|---|---|
| "I feel stuck in my career" | Life Guidance | Wisdom blocks by situation/emotion |
| "Tell me about Hanuman" | Story | Knowledge blocks by character/event |
| "Explain the concept of Maya" | Study | Both by concept/theme |

Stories are the primary teaching medium — not an optional add-on. When giving life guidance, the agent always tells the story that illustrates the teaching. Krishna teaches through Arjuna's crisis. Vyasa teaches through Yudhishthira's dilemmas. The story IS the lesson.

**Honesty protocol** (non-negotiable):
- Says "I don't have guidance on this" when the graph has no relevant blocks
- Distinguishes "the texts say this directly" from "based on related teachings..."
- Never fabricates verse references
- Never presents one darshana's view as universal truth
- Surfaces contradictions within the tradition when relevant

**Run the advisor:**
```bash
# Interactive chat
swarmkit chat workspace advisor

# Single question
swarmkit run workspace advisor --input "I keep procrastinating because I'm afraid of failure"
```

### Phase 4: Improvement

A separate agent reviews completed conversation sessions to find gaps and improve the knowledge graph over time. Never modifies the graph directly — all proposals go through human review.

What it looks for:
- **Gaps** — user asked something, advisor said "I don't have guidance." Are there verses in ChromaDB that could address this but aren't in the graph yet?
- **Effectiveness** — did the user engage (follow-up questions, went deeper) or disengage (changed topic, left)?
- **Missing connections** — user's question touched two themes that aren't connected in the graph
- **Depth mismatch** — advisor gave a philosophical answer when practical advice was needed
- **Citation accuracy** — do cited verses actually support the claims?

**Run session analysis:**
```bash
swarmkit run workspace session-analyst
```

---

## Governance

Three layers of quality control:

### 1. Wisdom Block Schema Gate
Validates structure before GBrain write. Required fields: `core_teaching`, `source_verses` (valid format), `applicable_when`, `depth_levels`, `teaching_story`. Catches malformed blocks before they enter the graph.

### 2. Knowledge Block Schema Gate
Validates story blocks. Required fields: `summary`, `story` (min 100 chars), `characters`, `significance`, `key_verses`. Ensures every knowledge block tells a real story, not an encyclopedia stub.

### 3. Citation Verifier Agent
Runs after every graph builder write and every advisor response. For each cited verse:
1. Pulls the actual verse from ChromaDB
2. Checks: does the verse support the claim?
3. Checks: is the translation accurately quoted?
4. Checks: is the darshana attribution correct?

Fails on misattribution — citing Shankaracharya's words as Ramanujacharya's is an error, not a warning. Sacred text misattribution is not a minor issue.

---

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| Agent Runtime | SwarmKit | Topology compiler, tool wiring, governance |
| Knowledge Graph | GBrain (PGLite) | Wisdom blocks, semantic edges, hybrid search |
| Citation Store | ChromaDB | 98K+ verses with translations and commentaries |
| LLM (Advisor) | Claude Sonnet 4 via OpenRouter | Conversational reasoning |
| LLM (Graph Builder) | Claude Sonnet 4 via OpenRouter | Verse interpretation, cross-text connection |
| LLM (Verifier) | DeepSeek Chat via OpenRouter | Citation accuracy checking |
| LLM (Ingestion) | DeepSeek Chat via OpenRouter | Text normalization |
| MCP Servers | scripture-search, gbrain serve | Tool access for agents |

### Why These Model Choices

- **Sonnet 4 for advisor and graph builder** — these need strong reasoning to connect cross-text themes and provide nuanced life guidance. Cheaper models produce superficial connections.
- **DeepSeek Chat for ingestion and verification** — mechanical tasks (normalize JSON, compare two texts). No deep reasoning needed, cost-effective.
- **All via OpenRouter** — single API key, model flexibility, cost tracking.

---

## Scripture Sources

### Tier 1: Structured JSON (ingested)

| Text | Repo | Documents | Commentators |
|---|---|---|---|
| Bhagavad Gita | vedicscriptures/bhagavad-gita | 719 verses | 21 (Shankara, Ramanuja, Madhva, Prabhupada, etc.) |
| Mahabharata | bhavykhatri/DharmicData | 73,817 shlokas | Sanskrit only (Ganguli translation planned) |
| Valmiki Ramayana | AshuVj/Valmiki_Ramayan_Dataset | 23,402 shlokas | Translation + explanation + comments |
| Chanakya Niti | gita/Datasets | 321 verses | English translation |
| Gita (backup) | kashishkhullar/gita_json | 700 verses | English only |

### Tier 2: Planned (needs scraping)

| Text | Source | Status |
|---|---|---|
| Gita commentaries (detailed) | gitasupersite.iitk.ac.in | Scraper needed |
| Upanishads (10-13 principal) | wisdomlib.org | Scraper needed |
| Puranas (Bhagavata, Vishnu, Shiva) | wisdomlib.org | Scraper needed |
| Yoga Sutras + Vyasa commentary | wisdomlib.org | Scraper needed |
| Mahabharata (Ganguli English) | sacred-texts.com | Scraper needed |

### Tier 3: Planned (PDF extraction)

| Text | Source |
|---|---|
| Arthashastra (Shamasastry) | archive.org |
| Vidura Niti (Ramaswamy Aiyar) | archive.org |
| Ramayana (Bibek Debroy critical edition) | archive.org |

---

## Workspace Structure

```
vedanta-advisor/
├── README.md
├── .gitignore                         # Excludes knowledge/ from git
├── workspace/
│   ├── workspace.yaml                 # MCP servers, governance, credentials
│   ├── topologies/
│   │   ├── ingest-scriptures.yaml     # Phase 1: parallel ingestion
│   │   ├── build-wisdom-graph.yaml    # Phase 2: single-agent graph building
│   │   ├── advisor.yaml              # Phase 3: conversational advisor
│   │   └── session-analyst.yaml      # Phase 4: improvement loop
│   ├── archetypes/
│   │   ├── scripture-ingester.yaml    # Reads texts, normalizes to schema
│   │   ├── graph-builder.yaml         # Connects verses by meaning
│   │   ├── wisdom-advisor.yaml        # Conversational agent
│   │   ├── session-analyst.yaml       # Reviews sessions
│   │   └── citation-verifier.yaml     # Verifies verse citations
│   ├── skills/
│   │   ├── store-verse.yaml           # Write to ChromaDB
│   │   ├── search-scripture.yaml      # Search ChromaDB
│   │   ├── get-verse.yaml             # Exact verse lookup
│   │   ├── brain-search.yaml          # GBrain hybrid search
│   │   ├── brain-write.yaml           # Write page to GBrain
│   │   ├── brain-get-relations.yaml   # Traverse graph edges
│   │   ├── verify-citations.yaml      # Citation accuracy check
│   │   └── write-notes.yaml           # Write to notes dir
│   ├── gates/
│   │   ├── wisdom-block.schema.json   # Structural validation
│   │   └── knowledge-block.schema.json
│   ├── scripts/
│   │   ├── fetch-tier1-datasets.sh    # Clone GitHub repos
│   │   ├── ingest-to-chromadb.py      # Load verses into ChromaDB
│   │   └── init-gbrain.sh            # Initialize GBrain
│   └── scripture_search_server.py     # ChromaDB MCP server
└── knowledge/                         # NOT in git
    ├── datasets/                      # Fetched GitHub repos
    ├── chromadb/                      # Verse collections
    ├── brain/                         # GBrain PGLite database
    ├── notes/                         # Agent output
    └── sessions/                      # Conversation logs
```

---

## Philosophical Approach

### Multiple Perspectives, No Favorites

Hindu philosophy has six major darshanas (schools of thought). The same verse gets different interpretations from each. This system presents multiple perspectives and lets the user decide — it never picks one as "correct."

| Darshana | Core View | Key Thinker |
|---|---|---|
| Advaita | Non-dual — you ARE Brahman, separation is illusion | Shankaracharya |
| Vishishtadvaita | Qualified non-dual — soul is real, part of Brahman | Ramanujacharya |
| Dvaita | Dual — God and soul are eternally distinct | Madhvacharya |
| Karma Yoga | Action without attachment to results | Gita emphasis |
| Bhakti | Devotion and surrender to the divine | Gaudiya tradition |
| Jnana | Knowledge and inquiry into the nature of self | Upanishadic emphasis |

### Stories as the Teaching Medium

Hindu tradition teaches through narrative. The Gita is a dialogue within a war. The Upanishads are conversations between teachers and students. The Mahabharata is 100,000 verses of ethical complexity with no easy answers.

The advisor always tells the story behind the teaching. A verse citation without its narrative context is just words. The story is what makes the teaching land.

### Sanskrit Preservation

Terms that don't translate cleanly — dharma, karma, moksha, samsara, atman, brahman — are used as-is with contextual explanation. "Dharma" is not "religion" or "duty" or "righteousness" — it's all three and more. The advisor uses the Sanskrit term and explains the relevant facet for the user's specific question.

### Honesty Over Completeness

The system says "I don't have guidance on this" rather than stretching a teaching to fit where it doesn't belong. An honest gap is better than a fabricated connection. Sacred text deserves that respect.

### Contradictions Are Features

The Gita says act with detachment. Some Upanishads say renounce action entirely. Both are valid within the tradition. The system surfaces these tensions explicitly rather than smoothing them over — because the tension itself is the teaching.

---

## Design Document

For the full architecture — including the wisdom block schema, knowledge block schema, graph edge types, ingestion pipeline details, and design decisions with rationale — see:

[design/details/vedanta-advisor.md](../../design/details/vedanta-advisor.md)
