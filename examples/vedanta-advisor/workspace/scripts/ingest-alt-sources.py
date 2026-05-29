# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27", "beautifulsoup4>=4.12"]
# ///
# ruff: noqa: E501
"""Ingest scriptures from GitaSupersite and Project Gutenberg into ChromaDB.

Covers texts that wisdomlib renders client-side (can't scrape with httpx).

Sources:
- GitaSupersite (IIT Kanpur): Brahma Sutras, Ashtavakra Gita
- Project Gutenberg: Vivekachudamani, Panchatantra, Hitopadesha, Dhammapada

Usage:
    cd examples/vedanta-advisor
    uv run workspace/scripts/ingest-alt-sources.py [--chromadb-dir DIR] [--text NAME] [--list] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
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
CHROMADB_DIR = os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb")


# ---------------------------------------------------------------------------
# GitaSupersite scrapers
# ---------------------------------------------------------------------------


def scrape_brahma_sutras(client: httpx.Client) -> list[dict]:
    """Scrape Brahma Sutras from GitaSupersite (4 chapters x 4 quarters)."""
    verses = []
    base = "https://www.gitasupersite.iitk.ac.in/brahmasutra_content"

    for chapter in range(1, 5):
        for quarter in range(1, 5):
            sutra = 0
            while True:
                sutra += 1
                url = f"{base}?language=ro&field_chapter_value={chapter}&field_quarter_value={quarter}&field_nsutra_value={sutra}"
                try:
                    r = client.get(url)
                    if r.status_code != 200:
                        break
                    soup = BeautifulSoup(r.text, "html.parser")
                    view = soup.find("div", class_=lambda c: c and "view-brahma-sutra" in c)
                    if not view:
                        break
                    text = view.get_text(separator=" ", strip=True)
                    if len(text) < 20 or f"{chapter}.{quarter}.{sutra}" not in text:
                        break
                    verses.append(
                        {
                            "id": f"brahma-sutras/{chapter}.{quarter}.{sutra}",
                            "text": text,
                            "chapter": f"Chapter {chapter}, Quarter {quarter}",
                            "verse": f"{chapter}.{quarter}.{sutra}",
                            "tradition": "vedanta",
                        }
                    )
                    print(f"  Brahma Sutra {chapter}.{quarter}.{sutra}")
                    time.sleep(0.5)
                except Exception:
                    break
    return verses


def scrape_ashtavakra_gita(client: httpx.Client) -> list[dict]:
    """Scrape Ashtavakra Gita from GitaSupersite (20 chapters)."""
    verses = []
    base = "https://www.gitasupersite.iitk.ac.in/minigita/ashtavakra"

    for chapter in range(1, 21):
        sutra = 0
        while True:
            sutra += 1
            url = f"{base}?language=ro&field_chapter_value={chapter}&field_nsutra_value={sutra}"
            try:
                r = client.get(url)
                if r.status_code != 200:
                    break
                soup = BeautifulSoup(r.text, "html.parser")
                rows = soup.find_all("div", class_=lambda c: c and "views-row" in str(c))
                if not rows:
                    break
                text = rows[0].get_text(separator=" ", strip=True)
                if len(text) < 10 or f"{chapter}.{sutra}" not in text:
                    break
                verses.append(
                    {
                        "id": f"ashtavakra-gita/{chapter}.{sutra}",
                        "text": text,
                        "chapter": f"Chapter {chapter}",
                        "verse": f"{chapter}.{sutra}",
                        "tradition": "advaita",
                    }
                )
                time.sleep(0.3)
            except Exception:
                break
        if sutra > 1:
            print(f"  Ashtavakra Gita ch{chapter}: {sutra - 1} verses")
    return verses


# ---------------------------------------------------------------------------
# Project Gutenberg ingesters
# ---------------------------------------------------------------------------


def _fetch_gutenberg(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text


def _strip_gutenberg_header_footer(text: str) -> str:
    """Remove Project Gutenberg header and footer boilerplate."""
    start = re.search(r"\*\*\* START OF.*?\*\*\*", text)
    end = re.search(r"\*\*\* END OF.*?\*\*\*", text)
    if start:
        text = text[start.end() :]
    if end:
        text = text[: end.start()]
    return text.strip()


def _split_into_sections(text: str, min_len: int = 50) -> list[tuple[str, str]]:
    """Split text into (heading, content) sections."""
    sections = []
    current_heading = "Introduction"
    current_content: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        # Detect headings: all caps lines, or lines starting with Roman numerals/numbers
        if stripped and (
            (stripped.isupper() and len(stripped) > 3 and len(stripped) < 80)
            or re.match(r"^(CHAPTER|BOOK|PART|SECTION|CANTO)\s", stripped, re.I)
            or re.match(r"^[IVXLC]+\.", stripped)
        ):
            if current_content:
                content = "\n".join(current_content).strip()
                if len(content) >= min_len:
                    sections.append((current_heading, content))
            current_heading = stripped
            current_content = []
        else:
            current_content.append(line)

    if current_content:
        content = "\n".join(current_content).strip()
        if len(content) >= min_len:
            sections.append((current_heading, content))

    return sections


def ingest_gutenberg_text(
    client: httpx.Client,
    name: str,
    url: str,
    collection_name: str,
    tradition: str,
    category: str,
) -> list[dict]:
    """Download and split a Project Gutenberg text into sections."""
    print(f"  Fetching {name} from Project Gutenberg...")
    raw = _fetch_gutenberg(client, url)
    text = _strip_gutenberg_header_footer(raw)
    sections = _split_into_sections(text)

    verses = []
    for i, (heading, content) in enumerate(sections):
        # Split long sections into ~500 char chunks for better retrieval
        chunks = [content[j : j + 500] for j in range(0, len(content), 450)]
        for ci, chunk in enumerate(chunks):
            if len(chunk.strip()) < 30:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower())[:40]
            doc_id = f"{name.lower().replace(' ', '-')}/{slug}/p{i + 1}"
            if len(chunks) > 1:
                doc_id += f"-c{ci + 1}"
            verses.append(
                {
                    "id": doc_id,
                    "text": chunk.strip(),
                    "chapter": heading,
                    "verse": str(i + 1),
                    "tradition": tradition,
                    "category": category,
                }
            )

    print(f"  {name}: {len(sections)} sections → {len(verses)} chunks")
    return verses


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


SOURCES = {
    "brahma-sutras": {
        "name": "Brahma Sutras",
        "type": "gitasupersite",
        "collection": "vedanta-texts",
        "tradition": "vedanta",
        "category": "philosophy",
        "description": "Foundational Vedanta text by Badarayana with Shankara Bhashya.",
    },
    "ashtavakra-gita": {
        "name": "Ashtavakra Gita",
        "type": "gitasupersite",
        "collection": "vedanta-texts",
        "tradition": "advaita",
        "category": "philosophy",
        "description": "Dialogue between Ashtavakra and King Janaka on absolute non-dualism.",
    },
    "vivekachudamani": {
        "name": "Vivekachudamani",
        "type": "gutenberg",
        "url": "https://www.gutenberg.org/cache/epub/55826/pg55826.txt",
        "collection": "shankaracharya",
        "tradition": "advaita",
        "category": "philosophy",
        "description": "Crest-Jewel of Discrimination by Shankaracharya.",
    },
    "panchatantra": {
        "name": "Panchatantra",
        "type": "gutenberg",
        "url": "https://www.gutenberg.org/cache/epub/25545/pg25545.txt",
        "collection": "wisdom-stories",
        "tradition": "dharmic",
        "category": "stories",
        "description": "Five books of animal fables (Arthur Ryder translation).",
    },
    "hitopadesha": {
        "name": "Hitopadesha",
        "type": "gutenberg",
        "url": "https://www.gutenberg.org/cache/epub/5765/pg5765.txt",
        "collection": "wisdom-stories",
        "tradition": "dharmic",
        "category": "stories",
        "description": "Book of Good Counsels (Sir Edwin Arnold translation).",
    },
    "dhammapada": {
        "name": "Dhammapada",
        "type": "gutenberg",
        "url": "https://www.gutenberg.org/cache/epub/2017/pg2017.txt",
        "collection": "buddhist",
        "tradition": "buddhist",
        "category": "ethics",
        "description": "Verses on the path (F. Max Muller translation, public domain).",
    },
}


def ingest_source(
    client: httpx.Client,
    chroma_client: chromadb.ClientAPI,
    key: str,
    source: dict,
    dry_run: bool = False,
) -> int:
    name = source["name"]
    src_type = source["type"]
    collection_name = source["collection"]

    print(f"\n{'=' * 60}")
    print(f"Ingesting: {name}")
    print(f"  {source['description']}")
    print(f"{'=' * 60}")

    if src_type == "gitasupersite":
        if key == "brahma-sutras":
            verses = scrape_brahma_sutras(client)
        elif key == "ashtavakra-gita":
            verses = scrape_ashtavakra_gita(client)
        else:
            print(f"  Unknown gitasupersite source: {key}")
            return 0
    elif src_type == "gutenberg":
        verses = ingest_gutenberg_text(
            client,
            name,
            source["url"],
            collection_name,
            source["tradition"],
            source["category"],
        )
    else:
        print(f"  Unknown source type: {src_type}")
        return 0

    if not verses:
        print(f"  No content found for {name}")
        return 0

    if dry_run:
        print(f"  [DRY RUN] Would upsert {len(verses)} documents")
        return len(verses)

    collection = chroma_client.get_or_create_collection(name=collection_name)
    batch_size = 100
    total = 0
    for i in range(0, len(verses), batch_size):
        batch = verses[i : i + batch_size]
        collection.upsert(
            ids=[v["id"] for v in batch],
            documents=[v["text"] for v in batch],
            metadatas=[
                {k: v for k, v in verse.items() if k not in ("id", "text")} for verse in batch
            ],
        )
        total += len(batch)

    print(f"  Upserted {total} documents to '{collection_name}'")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest scriptures from GitaSupersite and Project Gutenberg"
    )
    parser.add_argument("--chromadb-dir", default=CHROMADB_DIR)
    parser.add_argument("--text", help="Ingest only this text (use --list to see available)")
    parser.add_argument("--list", action="store_true", help="List available texts")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to ChromaDB")
    args = parser.parse_args()

    if args.list:
        print("Available texts:")
        for key, src in SOURCES.items():
            print(f"  {key:25s} {src['name']:30s} [{src['type']}]")
            print(f"  {'':25s} {src['description']}")
        return

    sources_to_ingest = SOURCES
    if args.text:
        if args.text not in SOURCES:
            print(f"Unknown: {args.text}. Use --list.", file=sys.stderr)
            sys.exit(1)
        sources_to_ingest = {args.text: SOURCES[args.text]}

    chromadb_path = Path(args.chromadb_dir).resolve()
    chromadb_path.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(chromadb_path))

    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
    )

    total = 0
    try:
        for key, source in sources_to_ingest.items():
            count = ingest_source(client, chroma_client, key, source, dry_run=args.dry_run)
            total += count
    finally:
        client.close()

    print(f"\n{'=' * 60}")
    print(f"Done. Total: {total} documents across {len(sources_to_ingest)} text(s)")
    print(f"ChromaDB: {chromadb_path}")


if __name__ == "__main__":
    main()
