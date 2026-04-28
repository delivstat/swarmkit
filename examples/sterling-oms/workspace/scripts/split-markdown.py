"""Split a large markdown file into per-section files.

Splits on ## headings. Each section becomes its own file, named
from the heading text. Good for ingesting large documentation
dumps into mcp-local-rag where per-file granularity matters for
search quality.

Usage:
    python scripts/split-markdown.py input.md --output ~/sterling-knowledge/product-docs/
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def split_markdown(input_path: Path, output_dir: Path, min_lines: int = 5) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    text = input_path.read_text(encoding="utf-8")

    sections: list[tuple[str, str]] = []
    current_heading = input_path.stem
    current_lines: list[str] = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_lines and len(current_lines) >= min_lines:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = line.lstrip("# ").strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines and len(current_lines) >= min_lines:
        sections.append((current_heading, "\n".join(current_lines)))

    for i, (heading, content) in enumerate(sections):
        filename = re.sub(r"[^\w\s-]", "", heading)[:80].strip().replace(" ", "-").lower()
        if not filename:
            filename = f"section-{i}"
        out_file = output_dir / f"{i:04d}-{filename}.md"
        out_file.write_text(f"# {heading}\n\n{content}", encoding="utf-8")

    return len(sections)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split large markdown into sections")
    parser.add_argument("input", type=Path, help="Input markdown file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("split-output"),
        help="Output directory",
    )
    parser.add_argument(
        "--min-lines",
        type=int,
        default=5,
        help="Skip sections shorter than this",
    )
    args = parser.parse_args()

    count = split_markdown(args.input, args.output, args.min_lines)
    print(f"Split into {count} files in {args.output}")
