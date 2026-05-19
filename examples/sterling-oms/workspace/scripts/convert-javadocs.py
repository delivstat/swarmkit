"""Convert standard Javadoc HTML files to markdown for RAG ingestion.

Handles core_javadocs and baseutils_doc — standard Java class/interface
Javadocs with class descriptions, method summaries, and method details.

Usage:
    python scripts/convert-javadocs.py /path/to/core_javadocs/ \
      --output ~/sterling-knowledge/product-docs/core-javadocs/

    python scripts/convert-javadocs.py /path/to/baseutils_doc/ \
      --output ~/sterling-knowledge/product-docs/core-javadocs/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_FILES = {
    "package-frame.html",
    "package-summary.html",
    "package-tree.html",
    "allclasses-frame.html",
    "allclasses-noframe.html",
    "overview-frame.html",
    "overview-summary.html",
    "overview-tree.html",
    "index-all.html",
    "index.html",
    "deprecated-list.html",
    "help-doc.html",
    "constant-values.html",
    "serialized-form.html",
}


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p\s*/?>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?li\s*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _convert_class(html_path: Path, source_name: str) -> str | None:  # noqa: PLR0912, PLR0915
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Class/interface name and kind
    title_match = re.search(r'<h2[^>]*class="title"[^>]*>(.*?)</h2>', html, re.DOTALL)
    if not title_match:
        return None
    title = _strip_tags(title_match.group(1)).strip()

    # Package
    pkg_match = re.search(r'<div class="subTitle">(.*?)</div>', html, re.DOTALL)
    package = _strip_tags(pkg_match.group(1)).strip() if pkg_match else ""

    # Class description
    desc_match = re.search(
        r'<div class="description">.*?<div class="block">(.*?)</div>',
        html,
        re.DOTALL,
    )
    description = _strip_tags(desc_match.group(1)).strip() if desc_match else ""

    # Superinterfaces / extends
    extends = ""
    ext_match = re.search(r"<dt>All Superinterfaces:</dt>\s*<dd>(.*?)</dd>", html, re.DOTALL)
    if ext_match:
        extends = _strip_tags(ext_match.group(1)).strip()
    ext_match2 = re.search(r"<dt>All Known Subinterfaces:</dt>\s*<dd>(.*?)</dd>", html, re.DOTALL)
    subinterfaces = ""
    if ext_match2:
        subinterfaces = _strip_tags(ext_match2.group(1)).strip()

    # Method summary rows
    methods: list[dict[str, str]] = []
    method_rows = re.findall(
        r"<tr[^>]*>\s*<td[^>]*><code>(.*?)</code></td>\s*"
        r'<td[^>]*><code>(.*?)</code>\s*(?:<div class="block">(.*?)</div>)?\s*</td>',
        html,
        re.DOTALL,
    )
    for ret_type, name_sig, desc in method_rows:
        methods.append(
            {
                "return_type": _strip_tags(ret_type).strip(),
                "signature": _strip_tags(name_sig).strip(),
                "description": _strip_tags(desc).strip() if desc else "",
            }
        )

    # Method details
    detail_blocks: list[dict[str, str]] = []
    for m in re.finditer(r"<h4>(.*?)</h4>\s*<pre>(.*?)</pre>", html, re.DOTALL):
        method_name = _strip_tags(m.group(1)).strip()
        signature = _strip_tags(m.group(2)).strip()
        desc_after = html[m.end() :]
        detail_desc_match = re.search(r'<div class="block">(.*?)</div>', desc_after, re.DOTALL)
        detail_desc = _strip_tags(detail_desc_match.group(1)).strip() if detail_desc_match else ""
        # Parameters
        params: list[str] = []
        param_section = re.search(
            r'<span class="paramLabel">Parameters:</span></dt>(.*?)(?=<dt>|</dl>)',
            desc_after,
            re.DOTALL,
        )
        if param_section:
            for pm in re.finditer(
                r"<code>(.*?)</code>\s*-\s*(.*?)(?=<dd>|</dl>|$)",
                param_section.group(1),
                re.DOTALL,
            ):
                params.append(
                    f"  - `{_strip_tags(pm.group(1)).strip()}` — {_strip_tags(pm.group(2)).strip()}"
                )

        # Returns
        ret_match = re.search(
            r'<span class="returnLabel">Returns:</span></dt>\s*<dd>(.*?)</dd>',
            desc_after,
            re.DOTALL,
        )
        returns = _strip_tags(ret_match.group(1)).strip() if ret_match else ""

        detail_blocks.append(
            {
                "name": method_name,
                "signature": signature,
                "description": detail_desc,
                "params": "\n".join(params),
                "returns": returns,
            }
        )

    # Build markdown
    lines = [f"# {title}", ""]
    if package:
        lines.append(f"**Package:** {package}")
    if extends:
        lines.append(f"**Extends:** {extends}")
    if subinterfaces:
        lines.append(f"**Known Subinterfaces:** {subinterfaces}")
    lines.append("")

    if description:
        lines += [description, ""]

    if methods:
        lines += ["## Methods", ""]
        for m in methods:
            sig = m["signature"]
            ret = m["return_type"]
            desc = m["description"]
            lines.append(f"- `{ret}` **{sig}**")
            if desc:
                lines.append(f"  {desc}")
        lines.append("")

    if detail_blocks:
        lines += ["## Method Details", ""]
        for d in detail_blocks:
            lines.append(f"### {d['name']}")
            lines.append(f"```java\n{d['signature']}\n```")
            if d["description"]:
                lines.append(d["description"])
            if d["params"]:
                lines.append(f"\n**Parameters:**\n{d['params']}")
            if d["returns"]:
                lines.append(f"\n**Returns:** {d['returns']}")
            lines.append("")

    lines.append(f"*Source: {source_name}*")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert standard Javadoc HTML to markdown")
    parser.add_argument("path", type=Path, help="Javadoc directory to convert")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: alongside source)",
    )
    args = parser.parse_args()

    if not args.path.is_dir():
        print(f"Not found: {args.path}")
        sys.exit(1)

    html_files = [
        f
        for f in args.path.rglob("*.html")
        if f.name not in SKIP_FILES and not f.name.endswith("-tree.html")
    ]

    if not html_files:
        print(f"No Javadoc HTML files found in {args.path}")
        return

    output_dir = args.output or args.path
    output_dir.mkdir(parents=True, exist_ok=True)

    converted = 0
    for hf in sorted(html_files):
        rel = hf.relative_to(args.path)
        source_name = str(rel)
        md = _convert_class(hf, source_name)
        if md:
            out_file = output_dir / rel.with_suffix(".md")
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(md, encoding="utf-8")
            print(f"  {out_file.relative_to(output_dir)}")
            converted += 1

    print(f"\nConverted {converted} Javadoc files to markdown")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
