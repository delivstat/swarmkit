# /// script
# requires-python = ">=3.11"
# dependencies = ["chromadb>=0.5"]
# ///
"""Ingest scripture datasets into ChromaDB collections.

Reads Tier 1 datasets (JSON from GitHub repos) and stores each verse
in the appropriate ChromaDB collection using the common verse schema.

Usage:
    uv run scripts/ingest-to-chromadb.py [--datasets-dir DIR] [--chromadb-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import chromadb
except ImportError:
    print("Install chromadb: pip install chromadb", file=sys.stderr)
    sys.exit(1)


DATASETS_DIR = os.environ.get("VEDANTA_DATASETS_DIR", "./knowledge/datasets")
CHROMADB_DIR = os.environ.get("VEDANTA_CHROMADB_DIR", "./knowledge/chromadb")

TRADITION_MAP = {
    "sankar": "advaita",
    "gambir": "advaita",
    "anand": "advaita",
    "ms": "advaita",
    "raman": "vishishtadvaita",
    "adi": "vishishtadvaita",
    "venkat": "vishishtadvaita",
    "madhav": "dvaita",
    "jaya": "dvaita",
    "vallabh": "shuddhadvaita",
    "abhinav": "kashmir-shaivism",
    "prabhu": "gaudiya-vaishnavism",
    "chinmay": "advaita",
    "siva": "advaita",
}


def ingest_gita(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Bhagavad Gita from vedicscriptures/bhagavad-gita dataset."""
    slok_dir = datasets_dir / "bhagavad-gita" / "slok"
    if not slok_dir.exists():
        print(f"  Gita dataset not found at {slok_dir}")
        return 0

    collection = client.get_or_create_collection("gita")
    count = 0

    for slok_file in sorted(slok_dir.glob("*.json")):
        with open(slok_file, encoding="utf-8") as f:
            verse = json.load(f)

        chapter = verse.get("chapter", 0)
        verse_num = verse.get("verse", 0)
        verse_id = f"gita:{chapter}:{verse_num}"

        translations = []
        commentaries = []
        skip_keys = {"_id", "chapter", "verse", "speaker", "slok", "transliteration"}

        for key, val in verse.items():
            if key in skip_keys or not isinstance(val, dict):
                continue
            author = val.get("author", key)
            tradition = TRADITION_MAP.get(key, "")
            et = val.get("et", "")
            ht = val.get("ht", "")
            sc = val.get("sc", "")

            if et:
                translations.append(
                    f"[{author}" + (f", {tradition}" if tradition else "") + f"]: {et}"
                )
            if sc:
                commentaries.append(
                    f"[{author}" + (f", {tradition}" if tradition else "") + f"]: {sc}"
                )
            elif ht and not et:
                translations.append(f"[{author}, Hindi]: {ht}")

        doc_parts = []
        sanskrit = verse.get("slok", "")
        if sanskrit:
            doc_parts.append(f"Sanskrit: {sanskrit}")
        translit = verse.get("transliteration", "")
        if translit:
            doc_parts.append(f"Transliteration: {translit}")
        speaker = verse.get("speaker", "")
        if speaker:
            doc_parts.append(f"Speaker: {speaker}")
        if translations:
            doc_parts.append("--- Translations ---")
            doc_parts.extend(translations)
        if commentaries:
            doc_parts.append("--- Commentaries ---")
            doc_parts.extend(commentaries)

        doc_text = "\n".join(doc_parts)

        collection.upsert(
            ids=[verse_id],
            documents=[doc_text],
            metadatas=[{
                "verse_id": verse_id,
                "text_family": "gita",
                "book": "Bhagavad Gita",
                "chapter": str(chapter),
                "verse": str(verse_num),
                "speaker": speaker,
            }],
        )
        count += 1

    return count


def ingest_ramayana(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Valmiki Ramayana from AshuVj dataset."""
    data_file = datasets_dir / "Valmiki_Ramayan_Dataset" / "data" / "Valmiki_Ramayan_Shlokas.json"
    if not data_file.exists():
        print(f"  Ramayana dataset not found at {data_file}")
        return 0

    collection = client.get_or_create_collection("ramayana")

    with open(data_file, encoding="utf-8") as f:
        shlokas = json.load(f)

    count = 0
    for shloka in shlokas:
        kanda = shloka.get("kanda", "")
        sarga = shloka.get("sarga", 0)
        shloka_num = shloka.get("shloka", 0)
        kanda_short = kanda.replace(" Kanda", "").lower().replace(" ", "-")
        verse_id = f"ramayana:{kanda_short}:{sarga}:{shloka_num}"

        doc_parts = []
        sanskrit = shloka.get("shloka_text", "")
        if sanskrit:
            doc_parts.append(f"Sanskrit: {sanskrit}")
        translit = shloka.get("transliteration", "")
        if translit:
            doc_parts.append(f"Transliteration: {translit}")
        translation = shloka.get("translation", "")
        if translation:
            doc_parts.append(f"Translation: {translation}")
        explanation = shloka.get("explanation", "")
        if explanation:
            doc_parts.append(f"Explanation: {explanation}")
        comments = shloka.get("comments", "")
        if comments:
            doc_parts.append(f"Commentary: {comments}")

        doc_text = "\n".join(doc_parts)

        collection.upsert(
            ids=[verse_id],
            documents=[doc_text],
            metadatas=[{
                "verse_id": verse_id,
                "text_family": "ramayana",
                "book": "Valmiki Ramayana",
                "kanda": kanda,
                "sarga": str(sarga),
                "shloka": str(shloka_num),
            }],
        )
        count += 1

    return count


def ingest_mahabharata(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Mahabharata from DharmicData dataset."""
    mb_dir = datasets_dir / "DharmicData" / "Mahabharata"
    if not mb_dir.exists():
        print(f"  Mahabharata dataset not found at {mb_dir}")
        return 0

    collection = client.get_or_create_collection("mahabharata")
    count = 0

    for book_file in sorted(mb_dir.glob("mahabharata_book_*.json")):
        with open(book_file, encoding="utf-8") as f:
            shlokas = json.load(f)

        for shloka in shlokas:
            book = shloka.get("book", 0)
            chapter = shloka.get("chapter", 0)
            shloka_num = shloka.get("shloka", 0)
            verse_id = f"mahabharata:{book}:{chapter}:{shloka_num}"

            text = shloka.get("text", "")
            doc_text = f"Sanskrit: {text}" if text else ""

            collection.upsert(
                ids=[verse_id],
                documents=[doc_text],
                metadatas=[{
                    "verse_id": verse_id,
                    "text_family": "mahabharata",
                    "book": "Mahabharata",
                    "parva": str(book),
                    "chapter": str(chapter),
                    "shloka": str(shloka_num),
                }],
            )
            count += 1

    return count


def ingest_chanakya(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Chanakya Niti from gita/Datasets."""
    cn_dir = datasets_dir / "Datasets" / "chanakya" / "Chanakya-Niti"
    if not cn_dir.exists():
        print(f"  Chanakya Niti dataset not found at {cn_dir}")
        return 0

    collection = client.get_or_create_collection("niti")
    count = 0

    for chapter_file in sorted(cn_dir.glob("*.json")):
        chapter_name = chapter_file.stem
        chapter_num = "".join(c for c in chapter_name if c.isdigit())

        with open(chapter_file, encoding="utf-8") as f:
            verses = json.load(f)

        for verse in verses:
            verse_num = verse.get("verse_id", count)
            verse_id = f"chanakya:{chapter_num}:{verse_num}"
            text = verse.get("text", "")

            collection.upsert(
                ids=[verse_id],
                documents=[text],
                metadatas=[{
                    "verse_id": verse_id,
                    "text_family": "niti",
                    "book": "Chanakya Niti",
                    "chapter": str(chapter_num),
                    "verse": str(verse_num),
                }],
            )
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest scriptures into ChromaDB")
    parser.add_argument("--datasets-dir", default=DATASETS_DIR)
    parser.add_argument("--chromadb-dir", default=CHROMADB_DIR)
    args = parser.parse_args()

    datasets_dir = Path(args.datasets_dir)
    chromadb_dir = Path(args.chromadb_dir)
    chromadb_dir.mkdir(parents=True, exist_ok=True)

    print(f"Datasets: {datasets_dir}")
    print(f"ChromaDB: {chromadb_dir}")
    print()

    client = chromadb.PersistentClient(path=str(chromadb_dir))

    print("[1/4] Ingesting Bhagavad Gita...")
    gita_count = ingest_gita(client, datasets_dir)
    print(f"  {gita_count} verses ingested")

    print("[2/4] Ingesting Valmiki Ramayana...")
    ramayana_count = ingest_ramayana(client, datasets_dir)
    print(f"  {ramayana_count} shlokas ingested")

    print("[3/4] Ingesting Mahabharata...")
    mb_count = ingest_mahabharata(client, datasets_dir)
    print(f"  {mb_count} shlokas ingested")

    print("[4/4] Ingesting Chanakya Niti...")
    cn_count = ingest_chanakya(client, datasets_dir)
    print(f"  {cn_count} verses ingested")

    print()
    total = gita_count + ramayana_count + mb_count + cn_count
    print(f"=== Done: {total:,} total documents ingested ===")
    print()
    print("Collections:")
    for coll in client.list_collections():
        print(f"  {coll.name}: {coll.count()} documents")


if __name__ == "__main__":
    main()
