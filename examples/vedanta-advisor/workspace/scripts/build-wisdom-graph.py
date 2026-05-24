# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27"]
# ///
"""Batch wisdom graph builder — bypasses the agent loop entirely.

For each theme/story, makes ONE LLM call to generate a wisdom or
knowledge block, then writes it to GBrain via CLI. No tool loop,
no growing context, no per-tool limits.

Usage:
    uv run scripts/build-wisdom-graph.py [--chromadb-dir DIR] [--model MODEL] [--dry-run]

Env:
    OPENROUTER_API_KEY — required for LLM calls
    VEDANTA_CHROMADB_DIR — ChromaDB path (default: ./knowledge/chromadb)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path

# Force unbuffered output so background runs show progress
print = partial(print, flush=True)

import httpx

try:
    import chromadb
except ImportError:
    print("Install chromadb: pip install chromadb", file=sys.stderr)
    sys.exit(1)


@dataclass
class BlockSpec:
    slug: str
    block_type: str  # "wisdom" or "knowledge"
    title: str
    description: str
    search_queries: list[str]
    verse_refs: list[str]


# ---- Block definitions ----

WISDOM_BLOCKS = [
    BlockSpec(
        slug="truth-and-honesty",
        block_type="wisdom",
        title="Truth and Honesty",
        description="Satya as dharma. Yudhishthira's commitment to truth, Harishchandra's story.",
        search_queries=["truth honesty satya dharma", "Yudhishthira truth"],
        verse_refs=["gita:2:16", "gita:16:2", "gita:17:15"],
    ),
    BlockSpec(
        slug="patience-and-endurance",
        block_type="wisdom",
        title="Patience and Endurance",
        description="Tapas, endurance through suffering. Draupadi's exile, Kunti's silent strength.",
        search_queries=["patience endurance tapas suffering", "exile endurance"],
        verse_refs=["gita:2:14", "gita:2:15", "gita:12:13"],
    ),
    BlockSpec(
        slug="relationships-and-family",
        block_type="wisdom",
        title="Relationships and Family",
        description="Family duty vs personal truth. Rama-Bharata, Karna-Kunti, bonds that define dharma.",
        search_queries=["family duty bond relationship", "brother loyalty"],
        verse_refs=["gita:1:28", "gita:1:37", "gita:2:6"],
    ),
    BlockSpec(
        slug="wealth-and-contentment",
        block_type="wisdom",
        title="Wealth and Contentment",
        description="Aparigraha, non-possessiveness. Contentment vs accumulation. Krishna-Sudama contrast.",
        search_queries=["wealth contentment greed possession", "renunciation possessions"],
        verse_refs=["gita:2:55", "gita:4:22", "gita:16:21"],
    ),
    BlockSpec(
        slug="leadership-and-service",
        block_type="wisdom",
        title="Leadership and Service",
        description="Rajadharma, servant leadership. Rama as ideal king, Yudhishthira's burden.",
        search_queries=["leadership king duty service", "rajadharma ruler"],
        verse_refs=["gita:3:21", "gita:3:22", "gita:3:25"],
    ),
    BlockSpec(
        slug="teachers-and-learning",
        block_type="wisdom",
        title="Teachers and Learning",
        description="Guru-shishya tradition. Approaching a teacher with humility. Knowledge as liberation.",
        search_queries=["teacher guru knowledge learning", "guru wisdom disciple"],
        verse_refs=["gita:4:34", "gita:4:38", "gita:4:39"],
    ),
    BlockSpec(
        slug="nature-of-happiness",
        block_type="wisdom",
        title="The Nature of Happiness",
        description="Ananda in Upanishads. Happiness vs pleasure. Lasting joy from within, not from objects.",
        search_queries=["happiness bliss ananda joy pleasure", "inner peace joy"],
        verse_refs=["gita:2:66", "gita:5:21", "gita:6:21"],
    ),
    BlockSpec(
        slug="karma-and-consequences",
        block_type="wisdom",
        title="Karma and Consequences",
        description="Actions have consequences across lifetimes. Karna's tragic arc. Cause and effect.",
        search_queries=["karma consequences action result", "karma fruit action"],
        verse_refs=["gita:4:17", "gita:18:12", "gita:3:13"],
    ),
    BlockSpec(
        slug="non-violence-and-compassion",
        block_type="wisdom",
        title="Non-Violence and Compassion",
        description="Ahimsa. Compassion for all beings. The tension between non-violence and righteous action.",
        search_queries=["non-violence compassion ahimsa beings", "compassion creatures"],
        verse_refs=["gita:16:2", "gita:12:13", "gita:6:32"],
    ),
    BlockSpec(
        slug="desire-and-attachment",
        block_type="wisdom",
        title="Desire and Attachment",
        description="Kama and raga. How desire binds, how detachment frees. Not suppression but understanding.",
        search_queries=["desire attachment craving binding", "desire enemy"],
        verse_refs=["gita:2:62", "gita:2:63", "gita:3:37"],
    ),
    BlockSpec(
        slug="food-and-discipline",
        block_type="wisdom",
        title="Food, Health and Discipline",
        description="Sattvic/rajasic/tamasic food. Discipline of body and senses. Moderation in all things.",
        search_queries=["food discipline senses body health", "sattvic food"],
        verse_refs=["gita:6:16", "gita:6:17", "gita:17:8"],
    ),
    BlockSpec(
        slug="women-in-dharma",
        block_type="wisdom",
        title="Women and Dharma",
        description="Sita's strength, Draupadi's fire, Savitri's wisdom, Kunti's sacrifice. Women as dharma-keepers.",
        search_queries=["women strength dharma Sita Draupadi", "feminine power"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="friendship",
        block_type="wisdom",
        title="Friendship",
        description="Krishna-Arjuna, Krishna-Sudama, Karna-Duryodhana. Bonds that transcend status.",
        search_queries=["friendship bond companion loyalty", "friend companion"],
        verse_refs=["gita:4:3", "gita:9:18", "gita:11:41"],
    ),
    BlockSpec(
        slug="old-age-and-wisdom",
        block_type="wisdom",
        title="Old Age and Wisdom",
        description="Bhishma's final teachings. What to let go of. Legacy and acceptance.",
        search_queries=["old age wisdom death legacy", "elder teaching"],
        verse_refs=["gita:2:13", "gita:2:22", "gita:8:5"],
    ),
    BlockSpec(
        slug="nature-and-environment",
        block_type="wisdom",
        title="Nature and the Divine",
        description="God manifests as nature. Rivers, mountains, trees as sacred. Ecological awareness in texts.",
        search_queries=["nature divine creation earth trees", "creation nature"],
        verse_refs=["gita:10:25", "gita:10:31", "gita:10:35"],
    ),
]

KNOWLEDGE_BLOCKS = [
    BlockSpec(
        slug="bhishma-on-deathbed",
        block_type="knowledge",
        title="Bhishma on the Bed of Arrows",
        description="Bhishma, pierced by countless arrows, lies on a bed of arrows and teaches Yudhishthira about dharma, kingship, and the nature of life. Shanti Parva and Anushasana Parva.",
        search_queries=["Bhishma arrows deathbed teaching", "Shanti Parva"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="karna-story",
        block_type="knowledge",
        title="The Tragedy of Karna",
        description="Karna's full arc: abandoned at birth, raised by a charioteer, rejected by Drona, befriended by Duryodhana, generous to a fault, cursed by fate, dies fighting against his own brothers.",
        search_queries=["Karna birth charioteer Duryodhana", "Karna generosity death"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="krishna-universal-form",
        block_type="knowledge",
        title="Krishna's Universal Form (Vishwarupa)",
        description="In Gita chapter 11, Arjuna asks to see Krishna's true form. Krishna reveals the vishwarupa — all of creation, all of time, all of destruction in one overwhelming vision.",
        search_queries=["vishwarupa universal form Krishna", "divine form chapter 11"],
        verse_refs=["gita:11:9", "gita:11:12", "gita:11:32"],
    ),
    BlockSpec(
        slug="savitri-and-satyavan",
        block_type="knowledge",
        title="Savitri and Satyavan",
        description="Savitri follows Yama, the god of death, when he takes her husband Satyavan's soul. Through wit, devotion, and persistence she wins him back — the only mortal to defeat Death with words.",
        search_queries=["Savitri Satyavan Yama death", "Savitri husband death"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="nachiketa-and-yama",
        block_type="knowledge",
        title="Nachiketa and Yama (Katha Upanishad)",
        description="A boy named Nachiketa is sent to the house of Death by his angry father. Yama offers him any boon. Nachiketa refuses wealth and pleasure — he wants to know: what happens after death?",
        search_queries=["Nachiketa Yama death knowledge", "Katha Upanishad boy"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="shabari-and-rama",
        block_type="knowledge",
        title="Shabari and Rama",
        description="An elderly tribal woman named Shabari waits decades for Rama to visit her hermitage. When he arrives, she offers him berries she has tasted first to ensure sweetness. Rama eats them with love.",
        search_queries=["Shabari Rama berries devotion", "Shabari tribal woman"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="vidura-niti",
        block_type="knowledge",
        title="Vidura's Counsel",
        description="Vidura, the wise half-brother, counsels blind king Dhritarashtra throughout the Mahabharata. His niti (statecraft and ethics) covers justice, leadership, human nature, and moral courage.",
        search_queries=["Vidura counsel Dhritarashtra wisdom", "Vidura niti"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="abhimanyu-chakravyuha",
        block_type="knowledge",
        title="Abhimanyu and the Chakravyuha",
        description="Arjuna's teenage son Abhimanyu knew how to enter the deadly Chakravyuha formation but not how to exit. He entered alone, fought seven great warriors, and died — the cost of incomplete knowledge.",
        search_queries=["Abhimanyu Chakravyuha formation", "Abhimanyu death battle"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="damayanti-and-nala",
        block_type="knowledge",
        title="Nala and Damayanti",
        description="King Nala loses his kingdom to gambling (parallels Yudhishthira), is separated from his wife Damayanti. Their reunion is a story of love surviving loss, exile, and deception.",
        search_queries=["Nala Damayanti gambling exile", "Nala kingdom loss"],
        verse_refs=[],
    ),
    BlockSpec(
        slug="bhagiratha-ganga",
        block_type="knowledge",
        title="Bhagiratha Brings the Ganga",
        description="King Bhagiratha performs severe penance to bring the river Ganga from heaven to earth to liberate the souls of his ancestors. Shiva catches her in his locks to prevent destruction.",
        search_queries=["Bhagiratha Ganga heaven penance", "Ganga river Shiva"],
        verse_refs=[],
    ),
]


def get_chromadb_client(chromadb_dir: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=chromadb_dir)


def fetch_verses(client: chromadb.ClientAPI, queries: list[str], refs: list[str], limit: int = 3) -> str:
    """Fetch verse context from ChromaDB — summary level."""
    parts = []

    for query in queries[:3]:
        for coll_name in ["gita", "ramayana", "mahabharata", "niti"]:
            try:
                coll = client.get_collection(coll_name)
                results = coll.query(query_texts=[query], n_results=limit, include=["documents", "metadatas"])
                for i, doc in enumerate(results["documents"][0]):
                    vid = results["metadatas"][0][i].get("verse_id", "unknown")
                    lines = doc.split("\n")
                    sanskrit = next((l for l in lines if l.startswith("Sanskrit:")), "")
                    translation = next((l for l in lines if l.startswith("Translation [")), "")
                    if sanskrit or translation:
                        parts.append(f"[{vid}]\n{sanskrit}\n{translation}")
            except Exception:
                continue

    for ref in refs[:5]:
        family = ref.split(":")[0]
        try:
            coll = client.get_collection(family)
            results = coll.get(ids=[ref], include=["documents"])
            if results["ids"]:
                doc = results["documents"][0]
                lines = doc.split("\n")
                sanskrit = next((l for l in lines if l.startswith("Sanskrit:")), "")
                transliteration = next((l for l in lines if l.startswith("Transliteration:")), "")
                translation = next((l for l in lines if l.startswith("Translation [")), "")
                speaker = next((l for l in lines if l.startswith("Speaker:")), "")
                parts.append(f"[{ref}]\n{sanskrit}\n{transliteration}\n{speaker}\n{translation}")
        except Exception:
            continue

    # Limit total context to ~3000 chars to avoid timeouts
    result = []
    total = 0
    for p in parts:
        if total + len(p) > 3000:
            break
        result.append(p)
        total += len(p)
    return "\n\n".join(result)


def generate_block(spec: BlockSpec, verse_context: str, api_key: str, model: str) -> str:
    """One LLM call to generate a complete block."""

    if spec.block_type == "wisdom":
        prompt = f"""Create a WISDOM BLOCK for the theme: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: wisdom
tags: [relevant, tags, here]
---

## Core Teaching
[Synthesized teaching from the verses above. 2-3 paragraphs.]

## Darshana Perspectives
### Karma Yoga
[Interpretation through action lens]
### Advaita
[Non-dual interpretation]
### Bhakti
[Devotion interpretation]

## Applicable When
- [specific life situation 1]
- [specific life situation 2]
- [specific life situation 3]
- [specific life situation 4]
- [specific life situation 5]

## Emotional States
- [emotion 1]
- [emotion 2]
- [emotion 3]

## Depth Levels
### Practical
[One-line actionable advice]
### Philosophical
[Deeper exploration]
### Contemplative
[Pointer for meditation/inquiry]

## Teaching Story
[A narrative from Mahabharata/Ramayana that illustrates this teaching. Tell it as a story, not a summary. 3-5 paragraphs.]

## Source Verses
- [verse:ref] — "[brief quote]" [primary/supporting/cross-text]

## Counter-Teaching
[What appears to contradict this, with resolution]

## Contradictions
[Genuine tensions within the tradition — present both sides]

RULES:
- Use actual verse references from the context provided
- Tag life situations from human experience, not abstract philosophy
- The teaching story should be vivid and narrative, not encyclopedic
- Sanskrit terms (dharma, karma, moksha etc.) use as-is with explanation"""

    else:
        prompt = f"""Create a KNOWLEDGE BLOCK for: "{spec.title}"
Description: {spec.description}

VERSE CONTEXT FROM SCRIPTURE:
{verse_context}

Write in this exact format:
---
title: {spec.title}
type: knowledge
tags: [relevant, tags, here]
---

## Summary
[Who/what this is, in 2-3 sentences]

## The Story
[Tell the FULL story as a narrative. What happened, who was involved, what choices were made, what the consequences were. Include dialogue where appropriate. 5-8 paragraphs. Make it vivid — this should be enjoyable to read, not an encyclopedia entry.]

## Characters
- [Character]: [role in this story]

## Significance
[Why this story matters — what values does it illustrate, what does it teach]

## Key Verses
- [verse:ref] — "[the most important shloka]"

## Connected Stories
- [what comes before/after in the narrative]

## Teachings Within
- [wisdom themes this story illustrates]

## Common Questions
- [questions people commonly ask about this]

RULES:
- Tell it as a story, not a summary
- Include emotional moments and dialogue
- Use actual verse references from the context provided
- Make it accessible to someone unfamiliar with Hindu texts"""

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 16000,
        },
        timeout=300.0,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"].get("content")
    if content is None:
        finish = data["choices"][0].get("finish_reason", "unknown")
        print(f"  WARNING: null content, finish_reason={finish}")
        # Check for thinking/reasoning models that put content elsewhere
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        if reasoning:
            return reasoning
        return f"[Generation failed: finish_reason={finish}]"
    return content


def write_to_gbrain(slug: str, content: str, dry_run: bool = False) -> bool:
    """Write a block to GBrain via CLI."""
    if dry_run:
        print(f"  [dry-run] would write {len(content)} chars to gbrain:{slug}")
        return True

    try:
        result = subprocess.run(
            ["gbrain", "put", slug],
            input=content,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            status = "created"
            try:
                data = json.loads(result.stdout)
                status = data.get("status", "unknown")
            except (json.JSONDecodeError, KeyError):
                pass
            print(f"  -> gbrain: {status}")
            return True
        else:
            print(f"  -> gbrain error: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  -> gbrain error: {e}")
        return False


def embed_all() -> None:
    """Run gbrain embed --all."""
    print("\nEmbedding all pages...")
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = env.get("OPENROUTER_API_KEY", "")
    env["OPENAI_BASE_URL"] = "https://openrouter.ai/api/v1"
    result = subprocess.run(
        ["gbrain", "embed", "--all"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"Embed error: {result.stderr[:200]}")


def get_existing_slugs() -> set[str]:
    """Get slugs already in GBrain."""
    try:
        result = subprocess.run(
            ["gbrain", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {line.split("\t")[0] for line in result.stdout.strip().split("\n") if line}
    except Exception:
        return set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch build wisdom graph")
    parser.add_argument("--chromadb-dir", default=os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb"))
    parser.add_argument("--model", default="moonshotai/kimi-k2.6")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't write to GBrain")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip blocks already in GBrain")
    parser.add_argument("--type", choices=["wisdom", "knowledge", "all"], default="all")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    chromadb_dir = Path(args.chromadb_dir)
    if not chromadb_dir.exists():
        print(f"Error: ChromaDB not found at {chromadb_dir}", file=sys.stderr)
        sys.exit(1)

    client = get_chromadb_client(str(chromadb_dir))
    existing = get_existing_slugs() if args.skip_existing else set()

    blocks: list[BlockSpec] = []
    if args.type in ("wisdom", "all"):
        blocks.extend(WISDOM_BLOCKS)
    if args.type in ("knowledge", "all"):
        blocks.extend(KNOWLEDGE_BLOCKS)

    total = len(blocks)
    skipped = 0
    created = 0
    failed = 0

    print(f"Building {total} blocks with {args.model}")
    print(f"ChromaDB: {chromadb_dir}")
    print(f"Existing blocks: {len(existing)}")
    print()

    for i, spec in enumerate(blocks, 1):
        if spec.slug in existing:
            print(f"[{i}/{total}] {spec.slug} — skipped (exists)")
            skipped += 1
            continue

        print(f"[{i}/{total}] {spec.slug} ({spec.block_type})...")

        verse_context = fetch_verses(client, spec.search_queries, spec.verse_refs)
        verse_count = verse_context.count("[gita:") + verse_context.count("[ramayana:") + verse_context.count("[mahabharata:")
        print(f"  verses: {verse_count} found, {len(verse_context)} chars context")

        try:
            content = generate_block(spec, verse_context, api_key, args.model)
            print(f"  generated: {len(content)} chars")

            if write_to_gbrain(spec.slug, content, dry_run=args.dry_run):
                created += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

        time.sleep(0.5)

    print(f"\n=== Done ===")
    print(f"Created: {created}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")
    print(f"Total in GBrain: {len(existing) + created}")

    if created > 0 and not args.dry_run:
        embed_all()


if __name__ == "__main__":
    main()
