# Vedanta Advisor — Pending Tasks

## Priority 1 (can run in parallel)

### 1. More wisdom blocks
Add new BlockSpec definitions to `workspace/scripts/build-wisdom-graph.py` and run.
Topics needed from real user questions:
- Dealing with death of a loved one
- Childhood trauma and healing
- Sibling rivalry
- Spiritual doubt and crisis of faith
- Purpose after retirement
- Loneliness in marriage
- Guilt and shame
- Letting go of someone you love
- Finding meaning in suffering
- Duty to parents vs own life

Run: `uv run workspace/scripts/build-wisdom-graph.py --model moonshotai/kimi-k2.6`

### 2. Upanishad English translations
Current Upanishad collection (755 mantras) is Sanskrit-only from IIT Kanpur CSV. English searches don't match.

Action: Write `workspace/scripts/scrape-wisdomlib.py` to scrape wisdomlib.org for:
- Chandogya, Brihadaranyaka, Katha, Isha, Kena, Mundaka, Mandukya, Taittiriya, Aitareya, Svetasvatara, Prashna
- Need: verse-level Sanskrit + English translation + Shankaracharya commentary
- Store in `upanishads` ChromaDB collection (upsert to add English alongside existing Sanskrit)

Source: https://www.wisdomlib.org/hinduism (well-structured HTML, verse-level pages)

### 3. GBrain brain-write skill fix
The `brain-write` skill sends `path` arg but GBrain MCP expects `slug`. The graph builder topology writes blocks that get silently skipped — we had to extract and import via CLI.

Fix options:
- a) Change the skill to use `slug` instead of `path` in the MCP tool mapping
- b) Write a thin wrapper MCP server that translates `path` → `slug` and proxies to gbrain
- c) Fix in the graph-builder archetype prompt to use `slug`

The simplest fix is (a) — check what GBrain's MCP `write_page` tool actually expects and match the skill definition.

## Priority 2

### 4. Session logging
Wire the advisor to log conversations for Phase 4 analysis.
- Option A: Log as GBrain pages (sessions become searchable)
- Option B: Write to `knowledge/sessions/` as JSON files
- The session-analyst topology and archetype are ready, just need the data

### 5. Chat mode testing
All tests used `swarmkit run` (single query). Need to test `swarmkit chat` for multi-turn conversation:
- Does the advisor remember prior turns?
- Does depth escalation work (casual → philosophical → contemplative)?
- Does language switching work mid-conversation?

### 6. Merge to main
Everything is on `design/vedanta-advisor` branch. Merge when stable.
Branch protection is re-enabled on main — needs PR.

## Priority 3

### 7. Tier 2 scraping (more sources)
- Mahabharata Ganguli translation (sacred-texts.com) — chapter-level HTML
- Yoga Sutras with Vyasa commentary (wisdomlib.org)
- Arthashastra (wisdomlib.org)

### 8. Voice interface
- Sarvam AI TTS (Bulbul V3) for Hindi/Indian language voice output
- Sarvam AI STT (Saarika V3) for voice input
- Would make the advisor accessible to non-English-literate users

### 9. Critic loop in compiler
Add a post-output critic loop to the SwarmKit compiler (not just governance skills):
- After agent produces output, a separate model call reviews it
- If critic finds issues, agent gets another turn with feedback
- Max 2-3 revision loops
- Config in topology YAML: `runtime: critic: enabled: true`

## Current Stats

- ChromaDB: 196,787 documents, 7 collections
- GBrain: 64 pages, 224 embedded chunks
- Sample outputs: 8 tested
- Governance: 3 layers (schema gates, citation verifier, quality critique)
- Languages: 23 supported (via IndicTrans2)
- Cost per query: ~$0.01-0.05 (Kimi K2.6)
