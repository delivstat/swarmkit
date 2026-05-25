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


def ingest_vedas(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Rig Veda, Yajur Veda, Atharva Veda from DharmicData."""
    collection = client.get_or_create_collection("vedas")
    count = 0

    veda_dirs = {
        "rigveda": ("Rigveda", "rigveda_mandala_*.json", "mandala"),
        "atharvaveda": ("AtharvaVeda", "atharvaveda_kaanda_*.json", "kaanda"),
        "yajurveda": ("Yajurveda", "vajasneyi_madhyadina_samhita.json", None),
    }

    for veda_id, (folder, pattern, unit_key) in veda_dirs.items():
        veda_dir = datasets_dir / "DharmicData" / folder
        if not veda_dir.exists():
            print(f"  {folder} not found, skipping")
            continue

        for data_file in sorted(veda_dir.glob(pattern)):
            with open(data_file, encoding="utf-8") as f:
                verses = json.load(f)

            if not isinstance(verses, list):
                continue

            for verse in verses:
                mandala = verse.get("mandala", verse.get("kaanda", verse.get("chapter", 0)))
                sukta = verse.get("sukta", verse.get("hymn", 0))
                text = verse.get("text", "")
                if not text.strip():
                    continue

                verse_id = f"{veda_id}:{mandala}:{sukta}:{count}"
                collection.upsert(
                    ids=[verse_id],
                    documents=[f"Sanskrit: {text}"],
                    metadatas=[{
                        "verse_id": verse_id,
                        "text_family": "vedas",
                        "book": veda_id.replace("veda", " Veda").title(),
                        "unit": str(mandala),
                        "sukta": str(sukta),
                    }],
                )
                count += 1

    return count


def ingest_upanishads(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Upanishads from hrgupta/indian-scriptures CSV dataset."""
    import csv

    up_dir = datasets_dir / "indian-scriptures" / "data" / "processed" / "upanishads"
    if not up_dir.exists():
        print(f"  Upanishads dataset not found at {up_dir}")
        return 0

    collection = client.get_or_create_collection("upanishads")
    count = 0

    for csv_file in sorted(up_dir.glob("*.csv")):
        upanishad_name = csv_file.stem.replace("_", " ").title()
        upanishad_slug = csv_file.stem.split("_upanishad")[0]

        with open(csv_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mantra = row.get("mantra", "")
                number = row.get("number", "").strip("।। ")
                if not mantra.strip():
                    continue

                verse_id = f"upanishad:{upanishad_slug}:{number}" if number else f"upanishad:{upanishad_slug}:{count}"
                collection.upsert(
                    ids=[verse_id],
                    documents=[f"Sanskrit: {mantra}"],
                    metadatas=[{
                        "verse_id": verse_id,
                        "text_family": "upanishads",
                        "book": upanishad_name,
                        "verse_number": number,
                    }],
                )
                count += 1

    return count


def ingest_itihasa_parallel(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Mahabharata parallel corpus (Sanskrit-English) from itihasa."""
    data_dir = datasets_dir / "itihasa" / "data"
    if not data_dir.exists():
        print(f"  Itihasa dataset not found at {data_dir}")
        return 0

    collection = client.get_or_create_collection("mahabharata_english")
    count = 0

    for split in ["train", "dev", "test"]:
        en_file = data_dir / f"{split}.en"
        sn_file = data_dir / f"{split}.sn"
        if not en_file.exists():
            continue

        en_lines = en_file.read_text(encoding="utf-8").strip().split("\n")
        sn_lines = sn_file.read_text(encoding="utf-8").strip().split("\n") if sn_file.exists() else []

        for i, en_line in enumerate(en_lines):
            if not en_line.strip():
                continue

            sn_line = sn_lines[i] if i < len(sn_lines) else ""
            verse_id = f"mahabharata-en:{split}:{i}"

            doc_parts = []
            if sn_line:
                doc_parts.append(f"Sanskrit: {sn_line}")
            doc_parts.append(f"Translation: {en_line}")

            collection.upsert(
                ids=[verse_id],
                documents=["\n".join(doc_parts)],
                metadatas=[{
                    "verse_id": verse_id,
                    "text_family": "mahabharata_english",
                    "book": "Mahabharata (Dutt)",
                    "split": split,
                    "line": str(i),
                }],
            )
            count += 1

    return count


def ingest_yoga_sutras(client: chromadb.ClientAPI, datasets_dir: Path) -> int:
    """Ingest Yoga Sutras from plain text (Johnston translation)."""
    txt_file = datasets_dir / "yoga-sutras-of-patanjali-llm-rag" / "YogaSutrasOfPatanjali-pg2526.txt"
    if not txt_file.exists():
        print(f"  Yoga Sutras not found at {txt_file}")
        return 0

    collection = client.get_or_create_collection("niti")
    content = txt_file.read_text(encoding="utf-8")

    lines = content.split("\n")
    count = 0
    current_book = 0
    current_sutra = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("BOOK "):
            current_book += 1
            current_sutra = 0
            continue
        if len(line) > 20 and not line.startswith(("The Project", "This ebook", "Title:", "Author:", "Editor:", "Release", "Language", "Credits", "***")):
            current_sutra += 1
            verse_id = f"yoga-sutra:{current_book}:{current_sutra}"
            collection.upsert(
                ids=[verse_id],
                documents=[f"Translation [Charles Johnston]: {line}"],
                metadatas=[{
                    "verse_id": verse_id,
                    "text_family": "niti",
                    "book": "Yoga Sutras of Patanjali",
                    "chapter": str(current_book),
                    "verse": str(current_sutra),
                }],
            )
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest scriptures into ChromaDB")
    parser.add_argument("--datasets-dir", default=DATASETS_DIR)
    parser.add_argument("--chromadb-dir", default=CHROMADB_DIR)
    parser.add_argument("--new-only", action="store_true", help="Skip collections that already exist")
    args = parser.parse_args()

    datasets_dir = Path(args.datasets_dir)
    chromadb_dir = Path(args.chromadb_dir)
    chromadb_dir.mkdir(parents=True, exist_ok=True)

    print(f"Datasets: {datasets_dir}", flush=True)
    print(f"ChromaDB: {chromadb_dir}", flush=True)
    if args.new_only:
        print("Mode: new collections only (skipping existing)", flush=True)
    print(flush=True)

    client = chromadb.PersistentClient(path=str(chromadb_dir))
    existing = {c.name for c in client.list_collections()}
    if args.new_only:
        print(f"Existing collections: {', '.join(sorted(existing))}", flush=True)

    def should_skip(name: str) -> bool:
        if args.new_only and name in existing:
            print(f"  skipped (already exists with {client.get_collection(name).count()} docs)", flush=True)
            return True
        return False

    print("[1/8] Ingesting Bhagavad Gita...", flush=True)
    gita_count = 0 if should_skip("gita") else ingest_gita(client, datasets_dir)
    if gita_count:
        print(f"  {gita_count} verses ingested", flush=True)

    print("[2/8] Ingesting Valmiki Ramayana...", flush=True)
    ramayana_count = 0 if should_skip("ramayana") else ingest_ramayana(client, datasets_dir)
    if ramayana_count:
        print(f"  {ramayana_count} shlokas ingested", flush=True)

    print("[3/8] Ingesting Mahabharata (Sanskrit)...", flush=True)
    mb_count = 0 if should_skip("mahabharata") else ingest_mahabharata(client, datasets_dir)
    if mb_count:
        print(f"  {mb_count} shlokas ingested", flush=True)

    print("[4/8] Ingesting Chanakya Niti...", flush=True)
    cn_count = 0 if should_skip("niti") else ingest_chanakya(client, datasets_dir)
    if cn_count:
        print(f"  {cn_count} verses ingested", flush=True)

    print("[5/8] Ingesting Vedas (Rig, Yajur, Atharva)...", flush=True)
    veda_count = 0 if should_skip("vedas") else ingest_vedas(client, datasets_dir)
    if veda_count:
        print(f"  {veda_count} hymns ingested", flush=True)

    print("[6/8] Ingesting Upanishads...", flush=True)
    upanishad_count = 0 if should_skip("upanishads") else ingest_upanishads(client, datasets_dir)
    if upanishad_count:
        print(f"  {upanishad_count} mantras ingested", flush=True)

    print("[7/8] Ingesting Mahabharata (English parallel)...", flush=True)
    itihasa_count = 0 if should_skip("mahabharata_english") else ingest_itihasa_parallel(client, datasets_dir)
    if itihasa_count:
        print(f"  {itihasa_count} parallel verses ingested", flush=True)

    print("[8/8] Ingesting Yoga Sutras...", flush=True)
    yoga_count = ingest_yoga_sutras(client, datasets_dir)
    if yoga_count:
        print(f"  {yoga_count} sutras ingested", flush=True)

    print(flush=True)
    total = gita_count + ramayana_count + mb_count + cn_count + veda_count + upanishad_count + itihasa_count + yoga_count
    print(f"=== Done: {total:,} total new documents ingested ===", flush=True)
    print(flush=True)
    print("Collections:", flush=True)
    for coll in client.list_collections():
        print(f"  {coll.name}: {coll.count()} documents", flush=True)


if __name__ == "__main__":
    main()
