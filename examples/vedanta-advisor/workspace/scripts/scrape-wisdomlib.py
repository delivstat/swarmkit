# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5", "httpx>=0.27", "beautifulsoup4>=4.12"]
# ///
"""Scrape Upanishad translations from wisdomlib.org into ChromaDB.

Fetches Sanskrit text, English translation, and Shankaracharya commentary
for each verse, then upserts into the `upanishads` ChromaDB collection
alongside existing Sanskrit-only data from IIT Kanpur.

Usage:
    uv run scripts/scrape-wisdomlib.py [--chromadb-dir DIR] [--upanishad NAME] [--dry-run] [--list]

Env:
    VEDANTA_CHROMADB_DIR — ChromaDB path (default: ./knowledge/chromadb)
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
from bs4 import BeautifulSoup, Tag

try:
    import chromadb
except ImportError:
    print("Install chromadb: pip install chromadb", file=sys.stderr)
    sys.exit(1)

print = partial(print, flush=True)

USER_AGENT = "VedantaAdvisor-Scraper/1.0 (educational; +https://github.com/delivstat/swarmkit)"
BASE_URL = "https://www.wisdomlib.org"
REQUEST_DELAY = 1.5  # seconds between requests


@dataclass
class UpanishadConfig:
    name: str
    slug: str  # wisdomlib URL slug
    toc_path: str  # path to table of contents page

    def toc_url(self) -> str:
        return f"{BASE_URL}{self.toc_path}"


@dataclass
class Verse:
    upanishad: str
    chapter: str
    section: str
    verse_number: str
    sanskrit: str = ""
    translation: str = ""
    commentary: str = ""
    source_url: str = ""

    @property
    def verse_id(self) -> str:
        parts = [self.upanishad]
        if self.chapter:
            parts.append(self.chapter)
        if self.section:
            parts.append(self.section)
        if self.verse_number:
            parts.append(self.verse_number)
        # Fallback: extract doc ID from URL to guarantee uniqueness
        if len(parts) == 1 and self.source_url:
            doc_match = re.search(r"doc(\d+)", self.source_url)
            if doc_match:
                parts.append(f"p{doc_match.group(1)}")
        return ":".join(parts)

    def to_document(self) -> str:
        parts = []
        if self.sanskrit:
            parts.append(f"Sanskrit: {self.sanskrit}")
        if self.translation:
            parts.append(f"Translation [English]: {self.translation}")
        if self.commentary:
            parts.append(f"Commentary [Shankaracharya]: {self.commentary}")
        parts.append("Source: wisdomlib.org")
        return "\n".join(parts)

    def to_metadata(self) -> dict:
        return {
            "verse_id": self.verse_id,
            "upanishad_name": self.upanishad,
            "chapter": self.chapter,
            "section": self.section,
            "verse_number": self.verse_number,
            "has_english": bool(self.translation),
            "has_commentary": bool(self.commentary),
            "source": "wisdomlib.org",
        }


UPANISHADS = [
    UpanishadConfig("chandogya", "chandogya-upanishad-english", "/hinduism/book/chandogya-upanishad-english"),
    UpanishadConfig("chandogya-shankara", "chandogya-upanishad-shankara-bhashya", "/hinduism/book/chandogya-upanishad-shankara-bhashya"),
    UpanishadConfig("brihadaranyaka", "the-brihadaranyaka-upanishad", "/hinduism/book/the-brihadaranyaka-upanishad"),
    UpanishadConfig("katha", "katha-upanishad-shankara-bhashya", "/hinduism/book/katha-upanishad-shankara-bhashya"),
    UpanishadConfig("isha", "ishavasya-upanishad-shankara-bhashya", "/hinduism/book/ishavasya-upanishad-shankara-bhashya"),
    UpanishadConfig("kena", "kena-upanishad-shankara-bhashya", "/hinduism/book/kena-upanishad-shankara-bhashya"),
    UpanishadConfig("mundaka", "mundaka-upanishad-shankara-bhashya", "/hinduism/book/mundaka-upanishad-shankara-bhashya"),
    UpanishadConfig("mandukya", "mandukya-upanishad-karika-bhashya", "/hinduism/book/mandukya-upanishad-karika-bhashya"),
    UpanishadConfig("taittiriya", "the-taittiriya-upanishad", "/hinduism/book/the-taittiriya-upanishad"),
    UpanishadConfig("prashna", "prashna-upanishad-shankara-bhashya", "/hinduism/book/prashna-upanishad-shankara-bhashya"),
    # Aitareya and Svetasvatara don't have dedicated book pages on wisdomlib — skip for now
]

UPANISHAD_MAP = {u.name: u for u in UPANISHADS}


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )


def fetch_with_retry(client: httpx.Client, url: str, max_retries: int = 3) -> httpx.Response | None:
    for attempt in range(max_retries):
        try:
            resp = client.get(url)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                print(f"  404: {url}")
                return None
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            print(f"  HTTP error {e.response.status_code} on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except httpx.RequestError as e:
            print(f"  request error: {e} on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def discover_verse_urls(client: httpx.Client, config: UpanishadConfig) -> list[str]:
    """Fetch the TOC page and extract all verse/mantra page URLs."""
    print(f"Discovering verses for {config.name}...")
    resp = fetch_with_retry(client, config.toc_url())
    if not resp:
        print(f"  failed to fetch TOC for {config.name}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    urls: list[str] = []

    # wisdomlib TOC pages have links to individual verse pages
    # Look for links containing "mantra" or "verse" or chapter links
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Match verse/mantra page patterns
        if config.slug in href and (
            "/d/" in href  # detail pages
            or re.search(r"verse-\d+|mantra-\d+|sloka-\d+", href)
            or re.search(r"chapter-\d+", href)
        ):
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            if full_url not in urls:
                urls.append(full_url)

    # If we only found a few section/chapter pages, drill into them to find verse pages
    # This handles TOCs that link to sections (Prashna I, II...) not individual verses
    if len(urls) <= 20:
        section_urls = list(urls)
        verse_urls: list[str] = []
        for sec_url in section_urls:
            time.sleep(REQUEST_DELAY)
            sec_resp = fetch_with_retry(client, sec_url)
            if not sec_resp:
                continue
            sec_soup = BeautifulSoup(sec_resp.text, "html.parser")
            for link in sec_soup.find_all("a", href=True):
                href = link["href"]
                if config.slug in href and "/d/" in href:
                    full = href if href.startswith("http") else f"{BASE_URL}{href}"
                    if full not in urls and full not in verse_urls:
                        verse_urls.append(full)
        if verse_urls:
            urls.extend(verse_urls)

    # Also try the book's content listing page
    if not urls:
        listing_url = f"{BASE_URL}/hinduism/book/{config.slug}/contents"
        resp2 = fetch_with_retry(client, listing_url)
        if resp2:
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            for link in soup2.find_all("a", href=True):
                href = link["href"]
                if config.slug in href and "/d/" in href:
                    full = href if href.startswith("http") else f"{BASE_URL}{href}"
                    if full not in urls:
                        urls.append(full)

    print(f"  found {len(urls)} verse pages")
    return urls


def parse_verse_number_from_url(url: str) -> tuple[str, str, str]:
    """Extract chapter, section, verse from URL path."""
    # Patterns: verse-1, mantra-1, chapter-1-verse-2, etc.
    chapter = ""
    section = ""
    verse = ""

    ch_match = re.search(r"chapter-(\d+)", url)
    if ch_match:
        chapter = ch_match.group(1)

    sec_match = re.search(r"section-(\d+)|khanda-(\d+)|adhyaya-(\d+)", url)
    if sec_match:
        section = next(g for g in sec_match.groups() if g)

    v_match = re.search(r"verse-(\d+)|mantra-(\d+)|sloka-(\d+)", url)
    if v_match:
        verse = next(g for g in v_match.groups() if g)

    # If only /d/NUMBER pattern
    if not verse:
        d_match = re.search(r"/d/(\d+)", url)
        if d_match:
            verse = d_match.group(1)

    return chapter, section, verse


def parse_verse_page(html: str, url: str, upanishad_name: str) -> list[Verse]:
    """Parse a wisdomlib verse page into Verse objects.

    Wisdomlib structure (observed from Isha Upanishad Shankara Bhashya):
    - Page title/heading: "Verse N - <topic>" or "Mantra N"
    - Blockquote: contains Sanskrit (devanagari + IAST) + English translation
    - Heading "Śaṅkara's Commentary" or similar
    - Paragraphs of commentary text
    - "Footnotes and references:" section at end

    Some pages are introductions/notes with no verse — skip those.
    """
    soup = BeautifulSoup(html, "html.parser")
    verses: list[Verse] = []

    chapter, section, verse_num = parse_verse_number_from_url(url)

    # Try to extract verse number from page heading if URL didn't have it
    page_title = ""
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = h.get_text(strip=True).lower()
        if any(w in text for w in ["verse", "mantra", "sloka", "sūtra", "sutra"]):
            page_title = h.get_text(strip=True)
            if not verse_num:
                m = re.search(r"(?:verse|mantra|sloka|sūtra|sutra)\s*(\d+(?:[.\-]\d+)*)", text)
                if m:
                    parts = re.split(r"[.\-]", m.group(1))
                    if len(parts) == 1:
                        verse_num = parts[0]
                    elif len(parts) == 2:
                        if not chapter:
                            chapter = parts[0]
                        verse_num = parts[1]
                    elif len(parts) >= 3:
                        if not chapter:
                            chapter = parts[0]
                        if not section:
                            section = parts[1]
                        verse_num = parts[2]
            break

    if not page_title:
        return verses

    # Extract Sanskrit + translation from blockquotes
    sanskrit_parts: list[str] = []
    translation_parts: list[str] = []
    commentary_parts: list[str] = []

    # Blockquotes contain the verse: devanagari, IAST transliteration, translation
    for bq in soup.find_all("blockquote"):
        bq_text = bq.get_text("\n", strip=True)
        for line in bq_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            has_devanagari = any("ऀ" <= c <= "ॿ" for c in line)
            has_iast = any(c in line for c in "āīūṛṝḷḹṃḥśṣṭḍṇñṅ")
            # Lines with || or | are verse markers (IAST transliteration)
            is_verse_line = "||" in line or ("|" in line and has_iast)

            if has_devanagari:
                sanskrit_parts.append(line)
            elif is_verse_line and has_iast:
                sanskrit_parts.append(line)
            elif line.startswith('"') or line.startswith('“'):
                translation_parts.append(line.strip('""“”'))
            elif not has_iast and len(line) > 15 and not line[0].isdigit():
                translation_parts.append(line)

    # Find commentary: everything after "Commentary" heading until "Footnotes"
    in_commentary = False
    for elem in soup.find_all(["h1", "h2", "h3", "h4", "p"]):
        if not isinstance(elem, Tag):
            continue
        text = elem.get_text(strip=True)
        if not text:
            continue

        if elem.name in ("h1", "h2", "h3", "h4"):
            lower = text.lower()
            if any(w in lower for w in ["commentary", "bhāṣya", "bhashya", "śaṅkara", "shankara"]):
                in_commentary = True
                continue
            elif any(w in lower for w in ["footnote", "reference", "further reading"]):
                in_commentary = False
                continue

        if in_commentary and elem.name == "p":
            commentary_parts.append(text)

    sanskrit = " ".join(sanskrit_parts).strip()
    translation = " ".join(translation_parts).strip()
    commentary = " ".join(commentary_parts).strip()

    if sanskrit or translation:
        v = Verse(
            upanishad=upanishad_name,
            chapter=chapter,
            section=section,
            verse_number=verse_num,
            sanskrit=sanskrit,
            translation=translation,
            commentary=commentary,
            source_url=url,
        )
        verses.append(v)

    return verses


def scrape_upanishad(client: httpx.Client, config: UpanishadConfig) -> list[Verse]:
    """Scrape all verses for a single Upanishad."""
    verse_urls = discover_verse_urls(client, config)
    all_verses: list[Verse] = []

    for i, url in enumerate(verse_urls, 1):
        time.sleep(REQUEST_DELAY)
        print(f"  [{i}/{len(verse_urls)}] {url.split('/')[-1]}...", end="")

        resp = fetch_with_retry(client, url)
        if not resp:
            print(" failed")
            continue

        verses = parse_verse_page(resp.text, url, config.name)
        if verses:
            print(f" {len(verses)} verse(s)")
            all_verses.extend(verses)
        else:
            print(" no verses found")

    return all_verses


def upsert_to_chromadb(client: chromadb.ClientAPI, verses: list[Verse], dry_run: bool = False) -> int:
    """Upsert verses into the upanishads collection."""
    if not verses:
        return 0

    collection = client.get_or_create_collection("upanishads")

    # Deduplicate by verse_id (keep last occurrence)
    seen: dict[str, int] = {}
    for i, v in enumerate(verses):
        seen[v.verse_id] = i
    verses = [verses[i] for i in sorted(seen.values())]

    # Batch upsert (ChromaDB supports up to ~5000 per call)
    batch_size = 100
    total = 0

    for start in range(0, len(verses), batch_size):
        batch = verses[start : start + batch_size]
        ids = [v.verse_id for v in batch]
        documents = [v.to_document() for v in batch]
        metadatas = [v.to_metadata() for v in batch]

        if dry_run:
            for v in batch:
                print(f"  [dry-run] {v.verse_id}: {len(v.to_document())} chars")
            total += len(batch)
            continue

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        total += len(batch)
        print(f"  upserted batch: {len(batch)} verses ({total} total)")

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Upanishad translations from wisdomlib.org")
    parser.add_argument("--chromadb-dir", default=os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb"))
    parser.add_argument("--upanishad", default="all", help="Specific upanishad name or 'all'")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write to ChromaDB")
    parser.add_argument("--list", action="store_true", dest="list_only", help="List available Upanishads and exit")
    args = parser.parse_args()

    if args.list_only:
        print("Available Upanishads:")
        for u in UPANISHADS:
            print(f"  {u.name:<20} {u.toc_url()}")
        return

    chromadb_dir = Path(args.chromadb_dir)
    if not chromadb_dir.exists() and not args.dry_run:
        print(f"Error: ChromaDB not found at {chromadb_dir}", file=sys.stderr)
        sys.exit(1)

    targets: list[UpanishadConfig] = []
    if args.upanishad == "all":
        targets = UPANISHADS
    elif args.upanishad in UPANISHAD_MAP:
        targets = [UPANISHAD_MAP[args.upanishad]]
    else:
        print(f"Unknown upanishad: {args.upanishad}", file=sys.stderr)
        print(f"Available: {', '.join(UPANISHAD_MAP.keys())}", file=sys.stderr)
        sys.exit(1)

    db_client = chromadb.PersistentClient(path=str(chromadb_dir)) if not args.dry_run else None
    http_client = make_client()

    total_scraped = 0
    total_upserted = 0

    print(f"Scraping {len(targets)} Upanishad(s)")
    print(f"ChromaDB: {chromadb_dir}")
    print(f"Delay: {REQUEST_DELAY}s between requests")
    print()

    for config in targets:
        print(f"=== {config.name.upper()} ===")
        verses = scrape_upanishad(http_client, config)
        total_scraped += len(verses)

        if verses and db_client:
            count = upsert_to_chromadb(db_client, verses, dry_run=args.dry_run)
            total_upserted += count
        elif verses and args.dry_run:
            total_upserted += len(verses)
            for v in verses[:3]:
                print(f"  sample: {v.verse_id}")
                print(f"    {v.to_document()[:200]}...")

        print()

    print(f"=== Done ===")
    print(f"Scraped: {total_scraped} verses")
    print(f"Upserted: {total_upserted} verses")


if __name__ == "__main__":
    main()
