"""Extract text from the SwarmKit design .docx into a markdown-ish .md file.

Regenerate the extracted copy whenever the source docx changes:

    python scripts/extract_design.py

The extracted file is for human/Claude reading convenience only — the .docx
remains the source of truth.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_DIR = REPO_ROOT / "design"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def extract(docx_path: Path, out_path: Path) -> None:
    with zipfile.ZipFile(docx_path) as z, z.open("word/document.xml") as f:
        content = f.read().decode("utf-8")

    root = ET.fromstring(content)
    body = root.find("w:body", NS)
    if body is None:
        raise RuntimeError("document.xml has no <w:body>")

    lines: list[str] = []
    for para in body.iter(f"{{{W_NS}}}p"):
        style = ""
        p_pr = para.find("w:pPr", NS)
        if p_pr is not None:
            p_style = p_pr.find("w:pStyle", NS)
            if p_style is not None:
                style = p_style.get(f"{{{W_NS}}}val", "")
        text = "".join(t.text or "" for t in para.iter(f"{{{W_NS}}}t"))
        if style.startswith("Heading") or "Title" in style:
            lines.append(f"\n## [{style}] {text}")
        else:
            lines.append(text)

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")


def main() -> int:
    docx_files = sorted(DESIGN_DIR.glob("SwarmKit-Design-*.docx"))
    if not docx_files:
        print("No SwarmKit-Design-*.docx found in design/", file=sys.stderr)
        return 1
    latest = docx_files[-1]
    out = DESIGN_DIR / (latest.stem + ".extracted.md")
    extract(latest, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
