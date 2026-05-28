# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27", "beautifulsoup4>=4.12"]
# ///
# ruff: noqa: E501
"""Scrape additional Indian scriptures into ChromaDB.

Adds texts not covered by the existing Upanishad and Gita ingesters:
- Brahma Sutras (Vedanta Sutras) — foundational Vedanta
- Vivekachudamani (Shankaracharya) — practical Advaita
- Ashtavakra Gita — radical non-dualism
- Tattva Bodha (Shankaracharya) — Vedanta primer
- Atma Bodha (Shankaracharya) — self-knowledge
- Narada Bhakti Sutras — devotion path
- Yoga Vasistha (selected chapters) — practical philosophy
- Panchatantra (selected stories) — wisdom through fables
- Arthashastra (selected chapters) — governance and strategy
- Devi Mahatmyam — inner strength
- Tirukural — Tamil ethics (1,330 couplets)
- Dhammapada — practical mindfulness

Sources: wisdomlib.org, sacred-texts.com (public domain)

Usage:
    cd examples/vedanta-advisor
    uv run workspace/scripts/scrape-additional-scriptures.py [--chromadb-dir DIR] [--text NAME] [--list] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

try:
    import chromadb
except ImportError:
    print("Install chromadb: pip install chromadb", file=sys.stderr)
    sys.exit(1)

print = partial(print, flush=True)

USER_AGENT = "VedantaAdvisor-Scraper/1.0 (educational; +https://github.com/delivstat/swarmkit)"
REQUEST_DELAY = 1.5


@dataclass
class TextConfig:
    name: str
    collection: str
    source: str
    base_url: str
    toc_path: str
    verse_css: str = "p"
    category: str = "philosophy"
    tradition: str = "vedanta"
    description: str = ""


@dataclass
class ScrapedVerse:
    text_name: str
    chapter: str
    verse_number: str
    content: str
    commentary: str = ""
    metadata: dict = field(default_factory=dict)


# Verified URLs — all return HTTP 200 on wisdomlib.org (checked 2026-05-28)
TEXTS: dict[str, TextConfig] = {
    "brahma-sutras": TextConfig(
        name="Brahma Sutras",
        collection="vedanta-texts",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/brahma-sutras",
        category="philosophy",
        tradition="vedanta",
        description="Foundational Vedanta text by Badarayana. Systematizes Upanishadic teachings.",
    ),
    "vivekachudamani": TextConfig(
        name="Vivekachudamani",
        collection="shankaracharya",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/vivekachudamani",
        category="philosophy",
        tradition="advaita",
        description="Crest-Jewel of Discrimination by Adi Shankaracharya. Practical guide to self-realization.",
    ),
    "ashtavakra-gita": TextConfig(
        name="Ashtavakra Gita",
        collection="vedanta-texts",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/ashtavakra-gita-sanskrit",
        category="philosophy",
        tradition="advaita",
        description="Dialogue between Ashtavakra and King Janaka on absolute non-dualism.",
    ),
    "yoga-vasistha": TextConfig(
        name="Yoga Vasistha",
        collection="vedanta-texts",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/yoga-vasistha-english",
        category="philosophy",
        tradition="advaita",
        description="Teachings of Vasistha to Rama on consciousness, free will, and liberation.",
    ),
    "arthashastra": TextConfig(
        name="Arthashastra",
        collection="shastras",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/kautilya-arthashastra",
        category="governance",
        tradition="dharmic",
        description="Kautilya's treatise on statecraft, strategy, economics, and governance.",
    ),
    "tirukural": TextConfig(
        name="Tirukural",
        collection="ethics",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/tirukkural-thirukkural",
        category="ethics",
        tradition="tamil",
        description="1,330 couplets by Thiruvalluvar on virtue, wealth, and love. Universal ethics.",
    ),
    "panchatantra": TextConfig(
        name="Panchatantra",
        collection="wisdom-stories",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/panchatantra-sanskrit",
        category="stories",
        tradition="dharmic",
        description="Five books of animal fables teaching practical wisdom, strategy, and ethics.",
    ),
    "hitopadesha": TextConfig(
        name="Hitopadesha",
        collection="wisdom-stories",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/the-book-of-good-counsels",
        category="stories",
        tradition="dharmic",
        description="Beneficial counsel — wisdom fables for relationships and social conduct.",
    ),
    "devi-bhagavata": TextConfig(
        name="Devi Bhagavata Purana",
        collection="devotional",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/devi-bhagavata-purana",
        category="devotion",
        tradition="shakta",
        description="Glory of the Divine Mother — includes Devi Mahatmyam. Inner strength and overcoming obstacles.",
    ),
    "markandeya-purana": TextConfig(
        name="Markandeya Purana",
        collection="devotional",
        source="wisdomlib",
        base_url="https://www.wisdomlib.org",
        toc_path="/hinduism/book/the-markandeya-purana",
        category="devotion",
        tradition="shakta",
        description="Contains the Devi Mahatmyam (chapters 78-90). Stories of divine feminine power.",
    ),
}

# Texts not on wisdomlib — available via separate GitHub repos or plain text files.
# These are ingested by the companion script ingest-plain-texts.py.
PLAIN_TEXT_SOURCES = {
    "tattva-bodha": {
        "name": "Tattva Bodha",
        "description": "Vedanta primer by Shankaracharya. What is atman, maya, the three bodies.",
        "source": "https://www.gitasupersite.iitk.ac.in/",
        "collection": "shankaracharya",
    },
    "atma-bodha": {
        "name": "Atma Bodha",
        "description": "Self-Knowledge in 68 verses by Shankaracharya.",
        "source": "https://www.gitasupersite.iitk.ac.in/",
        "collection": "shankaracharya",
    },
    "narada-bhakti-sutras": {
        "name": "Narada Bhakti Sutras",
        "description": "84 aphorisms on the path of devotion by sage Narada.",
        "source": "manually curated from public domain translations",
        "collection": "devotional",
    },
    "dhammapada": {
        "name": "Dhammapada",
        "description": "423 verses on mindfulness, suffering, and the path.",
        "source": "Project Gutenberg / F. Max Muller translation (public domain)",
        "collection": "buddhist",
    },
}


def fetch_page(client: httpx.Client, url: str) -> BeautifulSoup | None:
    try:
        resp = client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            print(f"  SKIP {url} (HTTP {resp.status_code})")
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ERROR {url}: {e}")
        return None


def discover_chapter_links(client: httpx.Client, config: TextConfig) -> list[tuple[str, str]]:
    toc_url = f"{config.base_url}{config.toc_path}"
    print(f"Fetching TOC: {toc_url}")
    soup = fetch_page(client, toc_url)
    if soup is None:
        return []

    links: list[tuple[str, str]] = []
    content = soup.find("div", class_="text-body") or soup.find("div", id="content") or soup
    for a in content.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str):
            continue
        if config.toc_path in href and href != config.toc_path:
            title = a.get_text(strip=True)
            if title and len(title) > 2:
                full_url = href if href.startswith("http") else f"{config.base_url}{href}"
                links.append((title, full_url))

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for title, url in links:
        if url not in seen:
            seen.add(url)
            deduped.append((title, url))

    print(f"  Found {len(deduped)} chapter links")
    return deduped


def scrape_chapter_verses(
    client: httpx.Client,
    config: TextConfig,
    chapter_title: str,
    chapter_url: str,
) -> list[ScrapedVerse]:
    soup = fetch_page(client, chapter_url)
    if soup is None:
        return []

    verses: list[ScrapedVerse] = []
    content = soup.find("div", class_="text-body") or soup.find("div", id="content") or soup

    text_blocks = content.find_all(
        ["blockquote", "p", "div"], class_=re.compile(r"verse|sloka|text|sutra", re.I)
    )

    if not text_blocks:
        text_blocks = content.find_all("blockquote")

    if not text_blocks:
        paragraphs = content.find_all("p")
        text_blocks = [p for p in paragraphs if len(p.get_text(strip=True)) > 30]

    verse_num = 0
    for block in text_blocks:
        text = block.get_text(strip=True)
        if len(text) < 10:
            continue
        verse_num += 1
        verses.append(
            ScrapedVerse(
                text_name=config.name,
                chapter=chapter_title,
                verse_number=str(verse_num),
                content=text,
                metadata={
                    "source_url": chapter_url,
                    "category": config.category,
                    "tradition": config.tradition,
                },
            )
        )

    return verses


def upsert_to_chromadb(
    collection: chromadb.Collection,
    config: TextConfig,
    verses: list[ScrapedVerse],
) -> int:
    if not verses:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    seen_ids: set[str] = set()
    for v in verses:
        slug = re.sub(r"[^a-z0-9]+", "-", v.chapter.lower())[:40]
        doc_id = f"{config.name.lower().replace(' ', '-')}/{slug}/v{v.verse_number}"

        if doc_id in seen_ids:
            doc_id = f"{doc_id}-{len(seen_ids)}"
        seen_ids.add(doc_id)

        doc_text = v.content
        if v.commentary:
            doc_text += f"\n\nCommentary: {v.commentary}"

        ids.append(doc_id)
        documents.append(doc_text)
        metadatas.append(
            {
                "text": config.name,
                "chapter": v.chapter,
                "verse": v.verse_number,
                "category": config.category,
                "tradition": config.tradition,
                **(v.metadata or {}),
            }
        )

    batch_size = 100
    total = 0
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_docs = documents[i : i + batch_size]
        batch_meta = metadatas[i : i + batch_size]
        collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_meta)
        total += len(batch_ids)

    return total


def scrape_text(
    client: httpx.Client,
    chroma_client: chromadb.ClientAPI,
    config: TextConfig,
    dry_run: bool = False,
) -> int:
    print(f"\n{'=' * 60}")
    print(f"Scraping: {config.name}")
    print(f"  {config.description}")
    print(f"  Collection: {config.collection}")
    print(f"  Source: {config.source}")
    print(f"{'=' * 60}")

    chapters = discover_chapter_links(client, config)
    if not chapters:
        print(f"  No chapters found for {config.name}")
        return 0

    all_verses: list[ScrapedVerse] = []

    for i, (title, url) in enumerate(chapters):
        print(f"  [{i + 1}/{len(chapters)}] {title}")
        verses = scrape_chapter_verses(client, config, title, url)
        print(f"    → {len(verses)} verse(s)")
        all_verses.extend(verses)
        time.sleep(REQUEST_DELAY)

    print(f"\n  Total: {len(all_verses)} verses from {len(chapters)} chapters")

    if dry_run:
        print("  [DRY RUN] Skipping ChromaDB upsert")
        return len(all_verses)

    collection = chroma_client.get_or_create_collection(
        name=config.collection,
        metadata={"description": config.description},
    )
    count = upsert_to_chromadb(collection, config, all_verses)
    print(f"  Upserted {count} documents to collection '{config.collection}'")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape additional Indian scriptures")
    parser.add_argument(
        "--chromadb-dir", default=os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb")
    )
    parser.add_argument("--text", help="Scrape only this text (use --list to see available)")
    parser.add_argument("--list", action="store_true", help="List available texts")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to ChromaDB")
    args = parser.parse_args()

    if args.list:
        print("Available texts:")
        for key, cfg in TEXTS.items():
            print(f"  {key:25s} {cfg.name:30s} [{cfg.category}/{cfg.tradition}]")
            print(f"  {'':25s} {cfg.description}")
        return

    texts_to_scrape = TEXTS
    if args.text:
        if args.text not in TEXTS:
            print(f"Unknown text: {args.text}. Use --list to see available.", file=sys.stderr)
            sys.exit(1)
        texts_to_scrape = {args.text: TEXTS[args.text]}

    chromadb_path = Path(args.chromadb_dir).resolve()
    chromadb_path.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(chromadb_path))

    http_client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
    )

    total_verses = 0
    try:
        for _key, config in texts_to_scrape.items():
            count = scrape_text(http_client, chroma_client, config, dry_run=args.dry_run)
            total_verses += count
    finally:
        http_client.close()

    print(f"\n{'=' * 60}")
    print(f"Done. Total: {total_verses} verses across {len(texts_to_scrape)} text(s)")
    print(f"ChromaDB: {chromadb_path}")


if __name__ == "__main__":
    main()
