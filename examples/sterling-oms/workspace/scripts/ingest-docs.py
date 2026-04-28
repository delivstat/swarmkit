"""Ingest Sterling documentation into mcp-local-rag.

Run once to build the vector index. Re-run when docs change.
No SwarmKit or MCP SDK dependency — uses subprocess to talk
to mcp-local-rag directly via its CLI.

Prerequisites:
    Node.js 18+ with npx

Usage:
    export STERLING_DOCS_DIR=~/sterling-knowledge
    python scripts/ingest-docs.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXTENSIONS = {
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".xml",
    ".properties",
    ".java",
    ".pdf",
    ".docx",
    ".xsl",
}
MAX_FILE_SIZE = 10_000_000  # 10MB


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

    files = [
        f
        for f in root.rglob("*")
        if f.is_file()
        and f.suffix.lower() in EXTENSIONS
        and f.stat().st_size < MAX_FILE_SIZE
        and "lancedb" not in str(f)
        and "node_modules" not in str(f)
    ]

    print(f"Found {len(files)} files to ingest in {base_dir}")

    if not files:
        print("No files found. Check your STERLING_DOCS_DIR path.")
        sys.exit(1)

    # Use npx to run mcp-local-rag's ingest via its CLI
    # mcp-local-rag doesn't have a CLI ingest mode, so we use
    # a small Node.js script that calls the MCP server's ingest_file tool
    ingest_script = _create_ingest_helper(root)

    try:
        result = subprocess.run(
            ["npx", "-y", "mcp-local-rag", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            print("mcp-local-rag not available. Install Node.js 18+ and npx.")
            sys.exit(1)
    except FileNotFoundError:
        print("npx not found. Install Node.js 18+.")
        sys.exit(1)

    # Write file list for the helper script
    file_list = root / ".ingest-files.json"
    file_list.write_text(
        json.dumps([str(f) for f in files]),
        encoding="utf-8",
    )

    print(f"Ingesting {len(files)} files...")
    print("This may take a while for large doc sets (17K files ≈ 10-24 hours).")
    print("The vector index persists — you only need to do this once.\n")

    try:
        result = subprocess.run(
            ["node", str(ingest_script)],
            env={**os.environ, "BASE_DIR": str(root)},
            timeout=86400,  # 24 hours
            check=False,
        )
        if result.returncode != 0:
            print(f"\nIngestion failed with exit code {result.returncode}")
            sys.exit(1)
    finally:
        file_list.unlink(missing_ok=True)
        ingest_script.unlink(missing_ok=True)

    print("\nIngestion complete.")
    print(f"Vector index stored at: {root}/lancedb/")
    print("The index loads instantly on subsequent mcp-local-rag starts.")


def _create_ingest_helper(root: Path) -> Path:
    """Create a temporary Node.js script that ingests files via MCP."""
    script = root / ".ingest-helper.mjs"
    script.write_text(
        """
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { readFileSync } from "fs";
import { spawn } from "child_process";

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
