# Vedanta Advisor — Pending Tasks

## Priority 1 — DONE (2025-05-25)

### 1. More wisdom blocks ✅
Added 10 new BlockSpec entries to `workspace/scripts/build-wisdom-graph.py` and ran the builder.
All 10 topics created in GBrain via Kimi K2.6:
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

Result: 16 new blocks created (10 wisdom + 6 knowledge that were missing), 0 failed.

### 2. Upanishad English translations ✅
Created `workspace/scripts/scrape-wisdomlib.py` — scrapes wisdomlib.org for verse-level
Sanskrit + English translation + Shankaracharya commentary. Upserts into ChromaDB `upanishads` collection.

Scraped 10 Upanishads (~960 verses with English translations):
- Chandogya (both plain + Shankara Bhashya editions)
- Brihadaranyaka, Katha, Isha, Kena, Mundaka, Mandukya, Taittiriya, Prashna

Not available on wisdomlib as dedicated books: Aitareya, Svetasvatara (moved to Priority 3).

### 3. GBrain brain-write skill fix ✅
Root cause: the skill mapped to `write_page` but GBrain MCP exposes `put_page`.
Fix: changed `tool: write_page` → `tool: put_page` in `workspace/skills/brain-write.yaml`.
The graph builder topology now writes blocks directly via MCP.

### 4. Public service security ✅ (bonus)
Added two governance decision skills for exposing the advisor as a public service:
- `relevance-gate` (pre_input, Haiku ~$0.001/query) — rejects off-topic queries
  and prompt injection attempts before the expensive pipeline runs
- `output-sanitizer` (post_output, Haiku) — blocks leakage of API keys, system prompts,
  internal config, file paths, MCP server details

Both wired into `workspace.yaml` scoped to the advisor topology.
Also added `pre_input` trigger to the SwarmKit runtime (PRs #258-#260 merged to main).

## Priority 2

### 5. Session logging
Wire the advisor to log conversations for Phase 4 analysis.
- Option A: Log as GBrain pages (sessions become searchable)
- Option B: Write to `knowledge/sessions/` as JSON files
- The session-analyst topology and archetype are ready, just need the data

### 6. Chat mode testing
All tests used `swarmkit run` (single query). Need to test `swarmkit chat` for multi-turn conversation:
- Does the advisor remember prior turns?
- Does depth escalation work (casual → philosophical → contemplative)?
- Does language switching work mid-conversation?

### 7. Merge to main
Everything is on `design/vedanta-advisor` branch. Merge when stable.
Branch protection is re-enabled on main — needs PR.

## Priority 3

### 8. Tier 2 scraping (more sources)
- Mahabharata Ganguli translation (sacred-texts.com) — chapter-level HTML
- Yoga Sutras with Vyasa commentary (wisdomlib.org)
- Arthashastra (wisdomlib.org)
- Aitareya Upanishad (no dedicated wisdomlib book page — find alternate source)
- Svetasvatara Upanishad (no dedicated wisdomlib book page — find alternate source)

### 9. Voice interface
- Sarvam AI TTS (Bulbul V3) for Hindi/Indian language voice output
- Sarvam AI STT (Saarika V3) for voice input
- Would make the advisor accessible to non-English-literate users

### 10. Critic loop in compiler
Add a post-output critic loop to the SwarmKit compiler (not just governance skills):
- After agent produces output, a separate model call reviews it
- If critic finds issues, agent gets another turn with feedback
- Max 2-3 revision loops
- Config in topology YAML: `runtime: critic: enabled: true`

## Current Stats

- ChromaDB: ~197,750 documents, 7 collections (+960 English Upanishad verses)
- GBrain: 74 pages, 264 embedded chunks (+16 new wisdom/knowledge blocks)
- Sample outputs: 13 tested
- Governance: 5 layers (schema gates, citation verifier, quality critique, relevance gate, output sanitizer)
- Languages: 23 supported (via IndicTrans2)
- Cost per query: ~$0.01-0.05 (Kimi K2.6) + ~$0.001 for relevance gate + ~$0.001 for sanitizer
