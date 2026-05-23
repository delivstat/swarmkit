# Vedanta Advisor — Hindu Scripture Knowledge System

**Status:** proposed
**Author:** Srijith Kartha
**Date:** 2026-05-23

---

## Goal

Build a conversational spiritual advisor grounded in Hindu religious texts. The system provides life guidance with exact verse citations, multiple philosophical perspectives, and honest acknowledgment of gaps. Not a chatbot with generic answers — a knowledge system that traces every recommendation to a specific shloka, commentary, or narrative episode.

## Non-goals

- Guru persona or devotional framing — the agent is a knowledgeable guide, not a spiritual authority
- Astrology, numerology, or ritual recommendation — philosophy and ethics only
- Denominational bias — Shaiva, Vaishnava, Shakta traditions represented equally
- Real-time web search during conversation — all knowledge is pre-ingested and graph-indexed

---

## Architecture

Four distinct phases, each a separate SwarmKit topology.

### Phase 1: Ingestion (parallel, independent agents)

Multiple agents run independently, each responsible for one text family. This is a genuine multi-agent use case — the texts are completely independent, no cross-source reasoning needed.

| Agent | Text Family | Source | Format |
|---|---|---|---|
| gita-ingester | Bhagavad Gita (700 verses + 21 translations + commentaries) | `vedicscriptures/bhagavad-gita` GitHub | JSON |
| upanishad-ingester | Principal Upanishads (10-13) | `wisdomlib.org` scrape + `sanskritdocuments.org` | HTML → structured |
| mahabharata-ingester | Mahabharata (18 parvas) | `bhavykhatri/DharmicData` GitHub | JSON |
| ramayana-ingester | Valmiki Ramayana | `AshuVj/Valmiki_Ramayan_Dataset` GitHub | JSON |
| purana-ingester | Bhagavata Purana, Vishnu Purana, Shiva Purana | `gita/Datasets` GitHub + `wisdomlib.org` scrape | JSON + HTML |
| niti-ingester | Yoga Sutras, Chanakya Niti, Vidura Niti, Arthashastra | `gita/Datasets` + `archive.org` PDF extraction | JSON + PDF |

Each ingester:
1. Fetches raw text from source
2. Normalizes to a common verse schema (see below)
3. Stores in text-specific ChromaDB collection
4. ChromaDB is the **citation source** — raw verses, translations, commentaries, exact references

#### Common verse schema

```json
{
  "id": "gita:2:47",
  "text_family": "bhagavad-gita",
  "book": "Bhagavad Gita",
  "chapter": 2,
  "verse": 47,
  "sanskrit": "कर्मण्येवाधिकारस्ते मा फलेषु कदाचन...",
  "transliteration": "karmanye vadhikaraste ma phaleshu kadachana...",
  "translations": [
    {
      "author": "Swami Gambhirananda",
      "tradition": "advaita",
      "text": "Your right is to action alone, never to its fruits..."
    },
    {
      "author": "Swami Adidevananda",
      "tradition": "vishishtadvaita",
      "text": "You have a right to action alone, not to its fruit..."
    }
  ],
  "commentaries": [
    {
      "author": "Shankaracharya",
      "tradition": "advaita",
      "text": "..."
    },
    {
      "author": "Ramanujacharya",
      "tradition": "vishishtadvaita",
      "text": "..."
    },
    {
      "author": "Madhvacharya",
      "tradition": "dvaita",
      "text": "..."
    }
  ],
  "context": {
    "speaker": "Krishna",
    "listener": "Arjuna",
    "narrative_context": "Arjuna has laid down arms, paralysed by the consequences of fighting his own family"
  }
}
```

### Phase 2: Graph Building (single agent, sequential reasoning)

A single agent reads ALL ChromaDB collections and builds the wisdom graph. This must be single-agent because connecting verses across texts requires one context window — the same lesson from the RT-727 experience.

The graph is the **reasoning source** — the conversational agent queries this, not ChromaDB.

#### Wisdom block schema

```json
{
  "id": "detachment-from-outcome",
  "theme": "acting without attachment",
  "core_teaching": "Action is your right, never the fruit. Pour yourself fully into the work and release the anxiety about what happens after.",
  "darshana_perspectives": [
    {
      "school": "karma-yoga",
      "interpretation": "Act with full effort but without clinging to results. The action itself is the practice."
    },
    {
      "school": "advaita",
      "interpretation": "Attachment to outcomes reinforces the ego's illusion of control. Detachment reveals the Self that was never bound."
    },
    {
      "school": "bhakti",
      "interpretation": "Offer every action and its result to God. The devotee acts as an instrument, not the doer."
    }
  ],
  "sources": [
    {"ref": "gita:2:47", "relevance": "primary"},
    {"ref": "gita:2:48", "relevance": "supporting"},
    {"ref": "gita:3:19", "relevance": "supporting"},
    {"ref": "isha:1", "relevance": "cross-text"}
  ],
  "applicable_when": [
    "procrastinating due to fear of failure",
    "obsessing over results at work",
    "comparing yourself to others' success",
    "starting a new venture or project",
    "doing the right thing but seeing no reward"
  ],
  "emotional_states": ["anxiety", "self-doubt", "envy", "frustration", "paralysis"],
  "life_domains": ["career", "creativity", "relationships", "spiritual-practice"],
  "depth_levels": {
    "practical": "Stop calculating outcomes. Do the work. The rest isn't yours to control.",
    "philosophical": "Attachment to fruit is attachment to the ego's fantasy of controlling time. Action happens in the present; fruit belongs to the future, which doesn't exist yet.",
    "contemplative": "Who is the one that wants the result? Inquire into the doer."
  },
  "story_context": "Arjuna wanted to drop his weapons and walk away. Not because he was lazy — because he was overwhelmed by the consequences. Krishna's response wasn't 'don't worry about it.' It was 'your worry about consequences is the actual problem, not the consequences themselves.'",
  "counter_teaching": {
    "text": "This doesn't mean be passive. Gita 3.8 says inaction is worse. The body itself cannot be sustained without action.",
    "source": "gita:3:8"
  },
  "contradictions": [
    {
      "text": "Some Upanishadic passages advocate complete renunciation of action (sannyasa), not just detachment from results.",
      "source": "mundaka:1:2:12",
      "resolution": "The Gita itself addresses this in Chapter 5 — both paths lead to liberation, but karma yoga (action with detachment) is superior for most people (5.2)."
    }
  ],
  "related_blocks": ["duty-vs-desire", "surrender-to-divine", "action-vs-inaction"],
  "cultural_context": "This teaching is often misquoted as justification for apathy. The original context is a warrior being told to fight, not a student being told not to study for exams."
}
```

#### Graph edges (semantic connections)

```
[detachment-from-outcome] --complements--> [duty-vs-desire]
[detachment-from-outcome] --tensions-with--> [complete-renunciation]
[detachment-from-outcome] --deepened-by--> [who-is-the-doer]
[detachment-from-outcome] --illustrated-by--> [arjuna-at-kurukshetra]
[detachment-from-outcome] --practical-application--> [work-without-anxiety]
```

Edge types:
- `complements` — teachings that reinforce each other
- `tensions-with` — teachings that appear to contradict (with resolution notes)
- `deepened-by` — a more advanced teaching that extends this one
- `illustrated-by` — a narrative episode that demonstrates the teaching
- `practical-application` — a specific life-domain application
- `same-verse-different-lens` — same source verse, different darshana interpretation

#### Graph building process

The single agent:
1. Reads verses from each ChromaDB collection
2. Groups verses by theme (not by text — cross-text grouping)
3. For each theme, creates a wisdom block with:
   - Core teaching synthesized from all relevant verses
   - Multiple darshana perspectives
   - Life situation tags (the key indexing layer)
   - Emotional state tags
   - Depth levels (practical → philosophical → contemplative)
   - Story contexts from Mahabharata/Ramayana
   - Counter-teachings and contradictions
4. Creates graph edges between related wisdom blocks
5. Human review checkpoint before finalizing

### Phase 3: Conversation (single agent, user-facing)

A single conversational agent with access to the wisdom graph. This must be single-agent — conversational reasoning with context from prior turns cannot be fragmented.

#### Conversation flow

```
User: "I keep putting off starting my own business because what if it fails"

Agent internally:
  1. Search graph by emotional state: [fear, self-doubt, procrastination]
  2. Search graph by life domain: [career, entrepreneurship]
  3. Search graph by situation: ["afraid to start", "fear of failure"]
  4. Matches: [detachment-from-outcome, courage-in-uncertainty, duty-vs-desire]
  5. Selects depth level based on user's framing (practical, not philosophical)
  6. Pulls exact verses from ChromaDB for citation

Agent response:
  "The Gita addresses this directly. In Chapter 2, Verse 47, Krishna tells
   Arjuna: 'कर्मण्येवाधिकारस्ते मा फलेषु कदाचन' — your right is to the
   action alone, never to its results.

   But this isn't about ignoring outcomes. It's about recognizing that the
   fear of failure is itself the obstacle, not the failure. Arjuna wasn't
   afraid of losing — he was afraid of the consequences of winning. Krishna's
   point: that fear belongs to a future that doesn't exist yet. The only
   thing that exists is the action in front of you.

   Practically: what's the smallest step you could take toward the business
   this week — one that doesn't require the whole thing to succeed?

   [Bhagavad Gita 2.47 | Shankaracharya's commentary: 'The ignorant man
   acts with attachment to results; the wise man acts without attachment,
   for the welfare of the world' (3.25)]"
```

#### Honesty protocol

The agent MUST:
- Say "I don't have specific guidance on this" when the graph has no relevant wisdom blocks
- Distinguish between "the texts address this directly" vs "this is my interpretation based on related teachings"
- Never fabricate verse references
- Never present one darshana's interpretation as universal truth
- Flag when a teaching has significant counter-arguments within the tradition

#### Session memory

Each conversation session is stored with:
- User messages (anonymized)
- Agent responses
- Which wisdom blocks were used
- Which verses were cited
- User satisfaction signal (explicit feedback or implicit from conversation flow)

### Phase 4: Improvement (offline, analytical)

A separate agent reviews completed conversation sessions to improve the knowledge graph.

#### Analysis targets

1. **Gap detection** — user asked about something, agent said "I don't have guidance." These gaps become candidates for new wisdom blocks.
2. **Effectiveness scoring** — which responses led to continued conversation (user engaged) vs which led to topic change or abandonment (user unsatisfied)?
3. **Missing connections** — user's question touched two themes that aren't connected in the graph. Suggests a new edge.
4. **Depth mismatch** — agent gave a philosophical answer when user wanted practical advice, or vice versa. Suggests better depth-level selection logic.
5. **Citation accuracy** — verify that cited verses actually support the teaching they're cited for.

#### Improvement loop

```
Session logs → Analyst agent → Improvement proposals → Human review → Graph updates
```

The analyst agent NEVER modifies the graph directly. All proposals go through human review. This is sacred text — automated modifications without human oversight are not acceptable.

---

## Data Sources

### Tier 1: Ready-to-ingest (structured JSON)

| Source | Repo/URL | Covers |
|---|---|---|
| `vedicscriptures/bhagavad-gita` | github.com | Gita: 700 verses, 21+ translations, commentaries |
| `bhavykhatri/DharmicData` | github.com | Mahabharata (18 parvas), Ramayana, Vedas |
| `AshuVj/Valmiki_Ramayan_Dataset` | github.com | Valmiki Ramayana: shlokas + translations |
| `gita/Datasets` | github.com | Bhagavata Purana, Chanakya Niti |
| `Abhaykoul/Ancient-Indian-Wisdom` | huggingface.co | Cross-text dataset |
| Bhagavad Gita API | vedicscriptures.github.io | Live API (ingest locally) |

### Tier 2: Needs scraping (HTML, verse-level)

| Source | URL | Covers |
|---|---|---|
| Gita Supersite | gitasupersite.iitk.ac.in | Shankara, Ramanuja, Madhva commentaries |
| WisdomLib | wisdomlib.org | Upanishads, Puranas, Yoga Sutras with commentaries |
| Sacred Texts | sacred-texts.com | Mahabharata (Ganguli), Upanishads (Muller) |

### Tier 3: PDF/EPUB extraction

| Source | URL | Covers |
|---|---|---|
| Archive.org | archive.org | Arthashastra, Vidura Niti, Ramayana (Debroy) |

---

## Storage Architecture

Two-layer storage: ChromaDB for raw citation data, GBrain for the semantic knowledge graph.

### GBrain (github.com/garrytan/gbrain)

GBrain is Garry Tan's (YC CEO) production-grade persistent memory system for AI agents. MIT licensed, TypeScript/Bun, Postgres-backed with pgvector. Key features we use:

- **Self-wiring entity extraction** — when a wisdom block references "Krishna", "Arjuna", "Gita 2.47", GBrain auto-creates typed graph edges without LLM calls
- **Hybrid search** — vector (HNSW) + BM25 keyword + reciprocal-rank fusion + graph-boosted ranking. +31.4 P@5 over vector-only search
- **Git-backed persistence** — the brain is a git repo of pages (markdown). Postgres is just the search index. If the DB dies, rebuild from git
- **MCP server exposure** — `npx gbrain mcp-serve` gives agents full read/write access
- **Typed graph edges** — we define custom edge types for scripture relationships

Custom edge types for Vedanta Advisor:

| Edge Type | Meaning | Example |
|---|---|---|
| `complements` | Teachings that reinforce each other | detachment ↔ duty |
| `tensions_with` | Apparent contradictions (with resolution) | action ↔ renunciation |
| `deepened_by` | More advanced teaching extending this one | detachment → who-is-the-doer |
| `illustrated_by` | Narrative episode demonstrating the teaching | detachment → arjuna-at-kurukshetra |
| `same_verse_different_lens` | Same verse, different darshana interpretation | gita-2-47-advaita ↔ gita-2-47-bhakti |
| `cited_in` | Verse referenced by a wisdom block | wisdom-block → verse-page |

### Storage layout

```
vedanta-advisor/
├── knowledge/
│   ├── chromadb/              # Citation source — raw verses per text family
│   │   ├── gita/
│   │   ├── upanishads/
│   │   ├── mahabharata/
│   │   ├── ramayana/
│   │   ├── puranas/
│   │   └── niti/
│   ├── brain/                 # GBrain repo — reasoning source
│   │   ├── .git/              # Git-backed persistence
│   │   ├── pages/             # Wisdom blocks as markdown pages
│   │   │   ├── detachment-from-outcome.md
│   │   │   ├── duty-vs-desire.md
│   │   │   ├── courage-in-uncertainty.md
│   │   │   └── ...
│   │   ├── verses/            # Verse reference pages (auto-linked by GBrain)
│   │   │   ├── gita-2-47.md
│   │   │   ├── isha-1.md
│   │   │   └── ...
│   │   └── gbrain.config.ts   # Custom edge types, entity patterns
│   └── sessions/              # Conversation logs for Phase 4 analysis
```

**ChromaDB = citation source.** Raw verses, translations, commentaries. Queried by exact reference (`gita:2:47`), not by semantic similarity. No RAG at conversation time.

**GBrain = reasoning source.** Wisdom blocks stored as markdown pages with YAML frontmatter. GBrain auto-extracts entity references (verse IDs, theme names, darshana labels) and creates typed graph edges. The hybrid search (vector + keyword + graph-boost) lets the conversational agent find relevant wisdom blocks by life situation, emotional state, or theme.

**Why GBrain over custom graph:**
- Production-proven at YC scale (146K pages, 24K people, 66 cron jobs)
- Self-wiring graph saves the graph builder agent from manually creating every edge — it focuses on wisdom blocks, GBrain handles entity linking
- Git-backed means the entire knowledge graph is version-controlled, diffable, and recoverable
- MCP server is built-in — no custom server to build and maintain
- Hybrid search gives better results than vector-only (+31.4 P@5)

**What GBrain does NOT do (the agent still does):**
- Semantic interpretation of verses — GBrain stores blocks, the graph builder agent creates them
- Life-situation tagging — pattern matching can't derive "this verse applies to career anxiety"
- Cross-text thematic connections — GBrain auto-links entities by name, but connecting Gita 2.47 to Isha Upanishad 1 by meaning requires the agent
- Contradiction detection — the agent identifies tensions between teachings

**Flow:**
```
GitHub JSON → ChromaDB (raw verses, translations, commentaries)
ChromaDB → Graph builder agent (reads, interprets, creates wisdom blocks)
Graph builder agent → GBrain pages (wisdom blocks as markdown with frontmatter)
GBrain auto-wiring → entity edges (verse refs, themes, darshanas linked automatically)
Graph builder agent → GBrain edges (semantic connections: complements, tensions_with, etc.)

User question → GBrain hybrid search (match life situation / emotional state / theme)
GBrain results → Advisor agent (reasons over connected wisdom blocks, selects depth)
Advisor needs exact verse text → ChromaDB lookup (citation only, deterministic)
```

---

## MCP Servers

| Server | Purpose | Phase | Implementation |
|---|---|---|---|
| gbrain | Wisdom graph — search, read, write pages, traverse edges | 2, 3, 4 | `npx gbrain mcp-serve` (built-in) |
| scripture-search | ChromaDB exact verse lookup across all collections | 2, 3 | Custom (same pattern as Sterling chromadb_server.py) |
| session-logger | Write/read conversation sessions | 3, 4 | Custom or GBrain (sessions as pages) |
| github-fetcher | Clone/pull Tier 1 datasets | 1 | Script-based, not persistent MCP |
| web-scraper | Scrape wisdomlib, sacred-texts, gitasupersite | 1 | Script-based, not persistent MCP |
| pdf-extractor | Extract text from archive.org PDFs/EPUBs | 1 | MarkItDown or custom |

Note: Phase 1 "servers" are really ingestion scripts that run once, not persistent MCP servers. Only gbrain, scripture-search, and session-logger run during conversation.

---

## SwarmKit Workspace Structure

```
vedanta-advisor/
├── workspace.yaml
├── topologies/
│   ├── ingest-scriptures.yaml     # Phase 1: parallel ingestion to ChromaDB
│   ├── build-wisdom-graph.yaml    # Phase 2: single-agent, ChromaDB → GBrain
│   ├── advisor.yaml               # Phase 3: conversational agent
│   └── session-analyst.yaml       # Phase 4: improvement loop
├── archetypes/
│   ├── scripture-ingester.yaml    # Reads texts, normalizes to common schema
│   ├── graph-builder.yaml         # Connects verses across texts by meaning
│   ├── wisdom-advisor.yaml        # Conversational agent with honesty protocol
│   └── session-analyst.yaml       # Reviews sessions, proposes improvements
├── skills/
│   ├── fetch-github-dataset.yaml
│   ├── scrape-wisdomlib.yaml
│   ├── scrape-sacred-texts.yaml
│   ├── extract-pdf.yaml
│   ├── store-verse.yaml           # Write to ChromaDB
│   ├── search-scripture.yaml      # Query ChromaDB by verse ref
│   ├── get-verse.yaml             # Exact verse lookup from ChromaDB
│   ├── brain-search.yaml          # GBrain hybrid search (via MCP)
│   ├── brain-write.yaml           # Write wisdom block to GBrain (via MCP)
│   ├── brain-get-relations.yaml   # Traverse GBrain graph edges (via MCP)
│   ├── log-session.yaml
│   └── get-session-analytics.yaml
├── scripts/
│   ├── fetch-tier1-datasets.sh    # Clone all GitHub repos
│   ├── scrape-tier2-sources.py    # Scrape HTML sources
│   ├── extract-tier3-pdfs.py      # PDF/EPUB extraction
│   ├── ingest-to-chromadb.py      # Load into ChromaDB collections
│   └── init-gbrain.sh             # Initialize GBrain repo + DB + custom edge types
├── mcp-servers/
│   └── scripture-search/          # ChromaDB verse lookup (custom)
│   # GBrain MCP server is built-in: `npx gbrain mcp-serve`
└── knowledge/
    ├── chromadb/                   # Citation source — raw verses
    ├── brain/                     # GBrain repo — wisdom graph
    └── sessions/                  # Conversation logs
```

---

## Key Design Decisions

### Why ChromaDB for citation, not RAG

RAG (retrieve-then-reason) fails for spiritual advice because:
1. Embedding similarity matches surface-level words, not deep meaning. "I'm afraid of failing" and "कर्मण्येवाधिकारस्ते" have zero embedding similarity but are deeply connected.
2. Retrieved chunks lack the interpretive layer — a raw verse without commentary and life-situation context is useless for advice.
3. The graph already contains the pre-reasoned connections. RAG would re-derive what the graph builder already figured out, badly, on every query.

ChromaDB stores raw text for citation lookup only. The graph stores meaning.

### Why GBrain over custom knowledge graph

GBrain provides the storage, indexing, and MCP server layer out of the box. Building a custom graph server (Postgres + pgvector + custom MCP) would take weeks and duplicate what GBrain already does. GBrain's self-wiring entity extraction is a bonus — it auto-links verse references and entity names without LLM calls, reducing the graph builder agent's workload.

The tradeoff: GBrain's schema is generic (pages + typed edges), not scripture-specific. The wisdom block structure lives in markdown frontmatter, not in a purpose-built schema. If we outgrow GBrain's model, we fork — the git-backed persistence means no data loss.

### Why single-agent for graph building

Same lesson as the single-agent vs multi-agent finding: connecting "Gita 2.47 on detachment" to "Isha Upanishad 1 on renunciation" to "Yudhishthira's dilemma in Vana Parva" requires one agent seeing all three in context. Multiple agents building separate sub-graphs would miss cross-text connections.

### Why single-agent for conversation

Conversational context builds across turns. "I tried detachment but it feels like giving up" — the agent needs to remember that 3 turns ago it recommended Gita 2.47 and now needs to address the counter-teaching from 3.8. Fragmenting this across agents would lose the thread.

### Why human review on all graph modifications

This is sacred text for over a billion people. An automated system that misinterprets a verse or creates a misleading connection between teachings could cause real harm. Every wisdom block and every graph edge goes through human review before entering the conversational agent's knowledge base.

### Handling contradictions

Hindu philosophy is not monolithic. The system explicitly surfaces contradictions rather than hiding them:
- Gita advocates action with detachment; some Upanishads advocate complete renunciation
- Dharmasutras prescribe social rules that conflict with the Gita's universalism
- Different darshanas interpret the same verse differently

The wisdom block schema has a `contradictions` field specifically for this. The advisor presents both sides when relevant, with resolution notes where tradition offers them.

### Sanskrit preservation

Sanskrit terms that don't translate cleanly (dharma, karma, moksha, samsara, atman, brahman) are used as-is with explanation. English equivalents lose meaning — "dharma" is not "religion" or "duty" or "righteousness", it's all three and more. The advisor uses the Sanskrit term and explains the relevant facet for the user's specific question.

---

## Risks

1. **Translation quality** — bad translations in, bad advice out. The Gita has 21+ English translations with significant variation. Need careful curation of which translations to trust for each text.
2. **Interpretive bias** — the graph builder agent will have its own biases in how it tags and connects verses. Human review mitigates but doesn't eliminate.
3. **Cultural sensitivity** — some teachings are historically situated (Manusmriti, certain Dharmashastra passages). The system needs a cultural context layer to distinguish timeless philosophical teachings from historically situated social rules.
4. **Depth mismatch** — giving a philosophical answer to someone seeking practical advice, or vice versa. The depth_levels field addresses this but the advisor agent needs good judgment about which level to select.
5. **Scope creep** — users will ask about astrology, rituals, specific deity worship. The system should stay in its lane (philosophy, ethics, life guidance) and honestly decline what it's not built for.

---

## Implementation Order

1. **Design review** — this document
2. **Workspace skeleton** — workspace.yaml, topologies, archetypes, skills
3. **GBrain setup** — init repo, configure custom edge types, verify MCP server works
4. **Tier 1 fetch scripts** — clone GitHub repos, verify data quality
5. **ChromaDB ingestion** — Gita first (richest dataset), then expand
6. **Scripture search MCP server** — verse lookup + basic search
7. **Graph builder topology** — single agent reads ChromaDB, writes wisdom blocks to GBrain. Gita first
8. **Verify GBrain graph** — check auto-wired edges, test hybrid search quality
9. **Advisor topology** — conversational agent queries GBrain, cites from ChromaDB
10. **Session logging** — capture conversations (as GBrain pages or separate store)
11. **Analyst topology** — improvement loop, proposes graph updates for human review
11. **Tier 2 scraping** — Upanishads, Puranas, Yoga Sutras
12. **Tier 3 extraction** — Arthashastra, Vidura Niti
13. **Expand graph** — incorporate new texts into wisdom blocks

Start with the Gita. 700 verses with rich commentaries is the perfect proving ground. If the system produces good advice from Gita alone, the architecture works. If it doesn't, adding more texts won't help.
