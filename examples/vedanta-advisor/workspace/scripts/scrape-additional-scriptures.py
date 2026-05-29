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


# Verified URLs — HTTP 200 AND server-side content (checked 2026-05-28).
# Some wisdomlib books render content client-side via JavaScript and
# cannot be scraped with httpx. Those are listed in JS_RENDERED_TEXTS below.
TEXTS: dict[str, TextConfig] = {
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

# Texts that need JavaScript rendering (wisdomlib loads content client-side).
# These require either: (1) playwright/selenium scraper, or (2) alternative
# plain text sources. Listed here for tracking. Use --list to show them.
JS_RENDERED_TEXTS = {
    "brahma-sutras": {
        "name": "Brahma Sutras",
        "wisdomlib": "/hinduism/book/brahma-sutras",
        "alt_source": "gitasupersite.iitk.ac.in or manually curated",
        "description": "Foundational Vedanta text. Needs JS-capable scraper.",
    },
    "vivekachudamani": {
        "name": "Vivekachudamani",
        "wisdomlib": "/hinduism/book/vivekachudamani",
        "alt_source": "gitasupersite.iitk.ac.in",
        "description": "Crest-Jewel of Discrimination (Shankaracharya). Needs JS-capable scraper.",
    },
    "ashtavakra-gita": {
        "name": "Ashtavakra Gita",
        "wisdomlib": "/hinduism/book/ashtavakra-gita-sanskrit",
        "alt_source": "multiple public domain translations available",
        "description": "Radical non-dualism. Needs JS-capable scraper.",
    },
    "panchatantra": {
        "name": "Panchatantra",
        "wisdomlib": "/hinduism/book/panchatantra-sanskrit",
        "alt_source": "Project Gutenberg has Arthur Ryder translation",
        "description": "Animal fables. Needs JS-capable scraper.",
    },
    "hitopadesha": {
        "name": "Hitopadesha",
        "wisdomlib": "/hinduism/book/the-book-of-good-counsels",
        "alt_source": "Project Gutenberg has Edwin Arnold translation",
        "description": "Wisdom fables. Needs JS-capable scraper.",
    },
    "devi-bhagavata": {
        "name": "Devi Bhagavata Purana",
        "wisdomlib": "/hinduism/book/devi-bhagavata-purana",
        "alt_source": "sacred-texts.com (needs browser)",
        "description": "Divine Mother. Needs JS-capable scraper.",
    },
    "tattva-bodha": {
        "name": "Tattva Bodha",
        "alt_source": "gitasupersite.iitk.ac.in",
        "description": "Vedanta primer (Shankaracharya). Not on wisdomlib.",
    },
    "atma-bodha": {
        "name": "Atma Bodha",
        "alt_source": "gitasupersite.iitk.ac.in",
        "description": "Self-Knowledge in 68 verses (Shankaracharya). Not on wisdomlib.",
    },
    "narada-bhakti-sutras": {
        "name": "Narada Bhakti Sutras",
        "alt_source": "manually curated from public domain translations",
        "description": "84 aphorisms on devotion. Not on wisdomlib.",
    },
    "dhammapada": {
        "name": "Dhammapada",
        "alt_source": "Project Gutenberg / F. Max Muller translation",
        "description": "423 Buddhist verses on mindfulness. Not on wisdomlib.",
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

    # Wisdomlib puts content in #scontent div (server-rendered books only)
    content = soup.find(id="scontent")
    if not content or not content.get_text(strip=True):
        # Fallback for pages without #scontent
        content = soup.find("div", class_=lambda c: c and "chapter-content" in c)
    if not content or not content.get_text(strip=True):
        return []

    # Split content into paragraphs — each meaningful paragraph is a "verse"
    paragraphs = content.find_all("p")
    if not paragraphs:
        # Some pages use direct text nodes instead of <p> tags
        raw_text = content.get_text(separator="\n").strip()
        blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]
    else:
        blocks = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]

    verse_num = 0
    for text in blocks:
        if len(text) < 20:
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
        print("Available texts (server-rendered, scrapeable):")
        for key, cfg in TEXTS.items():
            print(f"  {key:25s} {cfg.name:30s} [{cfg.category}/{cfg.tradition}]")
            print(f"  {'':25s} {cfg.description}")
        print()
        print("Pending texts (need JS-capable scraper or alt source):")
        for key, info in JS_RENDERED_TEXTS.items():
            print(f"  {key:25s} {info['name']:30s}")
            print(f"  {'':25s} {info['description']}")
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
