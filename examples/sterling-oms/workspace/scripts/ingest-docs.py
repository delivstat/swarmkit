"""Ingest Sterling documentation into mcp-local-rag.

Run once to build the vector index. Re-run when docs change.
No SwarmKit or MCP SDK dependency — uses subprocess to talk
to mcp-local-rag directly via its CLI.

mcp-local-rag natively supports: .md, .txt, .pdf, .docx
For other file types (.html, .java, .xml, .xsl, .properties),
this script converts them to .txt in a staging directory before
ingestion.

Prerequisites:
    Node.js 18+ with npx

Usage:
    export STERLING_DOCS_DIR=~/sterling-knowledge
    python scripts/ingest-docs.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Files mcp-local-rag handles natively
NATIVE_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}

# Files we convert to .txt before ingestion
CONVERT_EXTENSIONS = {".html", ".htm", ".java", ".xml", ".xsl", ".properties"}

ALL_EXTENSIONS = NATIVE_EXTENSIONS | CONVERT_EXTENSIONS
MAX_FILE_SIZE = 10_000_000  # 10MB


def _strip_html(text: str) -> str:
    """Simple HTML tag removal without external dependencies."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _convert_file(src: Path, staging_dir: Path, root: Path) -> Path | None:
    """Convert a non-native file to .txt in the staging directory."""
    try:
        content = src.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    suffix = src.suffix.lower()

    if suffix in {".html", ".htm"}:
        content = _strip_html(content)
        header = f"# {src.stem}\n\nSource: {src.relative_to(root)}\n\n"
        content = header + content
    elif suffix == ".java":
        header = f"// Source: {src.relative_to(root)}\n\n"
        content = header + content
    elif suffix in {".xml", ".xsl"}:
        header = f"<!-- Source: {src.relative_to(root)} -->\n\n"
        content = header + content
    else:
        header = f"# {src.stem}\n\nSource: {src.relative_to(root)}\n\n"
        content = header + content

    # Preserve directory structure in staging
    rel = src.relative_to(root)
    out = staging_dir / rel.with_suffix(".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out


def main() -> None:
    base_dir = os.environ.get(
        "STERLING_DOCS_DIR",
        os.path.expanduser("~/sterling-knowledge"),
    )
    root = Path(base_dir)

    if not root.is_dir():
        print(f"Directory not found: {base_dir}")
        print("Set STERLING_DOCS_DIR to your knowledge directory.")
        sys.exit(1)

    all_files = [
        f
        for f in root.rglob("*")
        if f.is_file()
        and f.suffix.lower() in ALL_EXTENSIONS
        and f.stat().st_size < MAX_FILE_SIZE
        and "lancedb" not in str(f)
        and "node_modules" not in str(f)
        and ".ingest-staging" not in str(f)
    ]

    native_files = [f for f in all_files if f.suffix.lower() in NATIVE_EXTENSIONS]
    convert_files = [f for f in all_files if f.suffix.lower() in CONVERT_EXTENSIONS]

    print(f"Found {len(all_files)} files in {base_dir}")
    print(f"  Native ({', '.join(sorted(NATIVE_EXTENSIONS))}): {len(native_files)}")
    print(f"  Convert to .txt ({', '.join(sorted(CONVERT_EXTENSIONS))}): {len(convert_files)}")

    if not all_files:
        print("No files found. Check your STERLING_DOCS_DIR path.")
        sys.exit(1)

    # Convert non-native files to .txt in a staging directory
    staging_dir = root / ".ingest-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    converted: list[Path] = []
    if convert_files:
        print(f"\nConverting {len(convert_files)} files to .txt...")
        for f in convert_files:
            out = _convert_file(f, staging_dir, root)
            if out:
                converted.append(out)
        print(f"  Converted {len(converted)} files to {staging_dir}")

    # Build the final ingest list: native files + converted files
    ingest_files = native_files + converted
    print(f"\nTotal files to ingest: {len(ingest_files)}")

    # Create the Node.js helper script
    ingest_script = _create_ingest_helper(root)

    # Write file list
    file_list = root / ".ingest-files.json"
    file_list.write_text(
        json.dumps([str(f) for f in ingest_files]),
        encoding="utf-8",
    )

    print("Ingesting...")
    print("This may take a while for large doc sets (17K files = 10-24 hours).")
    print("The vector index persists — you only need to do this once.\n")

    try:
        result = subprocess.run(
            ["node", str(ingest_script)],
            env={**os.environ, "BASE_DIR": str(root)},
            timeout=86400,
            check=False,
        )
        if result.returncode != 0:
            print(f"\nIngestion failed with exit code {result.returncode}")
            sys.exit(1)
    finally:
        file_list.unlink(missing_ok=True)
        ingest_script.unlink(missing_ok=True)
        # Clean up staging directory
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

    print("\nIngestion complete.")
    print(f"Vector index stored at: {root}/lancedb/")


def _create_ingest_helper(root: Path) -> Path:
    """Create a temporary Node.js script that ingests files via MCP."""
    script = root / ".ingest-helper.mjs"
    script.write_text(
        """
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { readFileSync } from "fs";

const baseDir = process.env.BASE_DIR;
const files = JSON.parse(readFileSync(`${baseDir}/.ingest-files.json`, "utf-8"));

const transport = new StdioClientTransport({
  command: "npx",
  args: ["-y", "mcp-local-rag"],
  env: { ...process.env, BASE_DIR: baseDir },
});

const client = new Client({ name: "ingest", version: "1.0" });
await client.connect(transport);

let ok = 0, fail = 0;
for (let i = 0; i < files.length; i++) {
  try {
    await client.callTool({ name: "ingest_file", arguments: { filePath: files[i] } });
    ok++;
  } catch (e) {
    fail++;
  }
  if ((i + 1) % 50 === 0) {
    console.log(`  [${i + 1}/${files.length}] ${ok} ok, ${fail} failed`);
  }
}

console.log(`\\nDone: ${ok} ingested, ${fail} failed out of ${files.length}`);
await client.close();
process.exit(0);
""",
        encoding="utf-8",
    )
    return script


if __name__ == "__main__":
    main()
