"""Ingest Sterling documentation into mcp-local-rag vector store.

Run once to build the vector index. Re-run when docs change.
Pure Python — no MCP SDK dependency. Speaks JSON-RPC (MCP protocol)
directly over stdin/stdout to the mcp-local-rag process.

Ingests documentation files only: .md, .txt, .pdf, .docx
HTML files are converted to .txt before ingestion (mcp-local-rag
does not handle HTML natively).
Code files (.java, .xml, .xsl) belong on the filesystem — the
developer agent reads those directly.

Prerequisites:
    Node.js 18+ with npx

Usage:
    export STERLING_DOCS_DIR=~/sterling-knowledge/product-docs
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

NATIVE_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
HTML_EXTENSIONS = {".html", ".htm"}
ALL_EXTENSIONS = NATIVE_EXTENSIONS | HTML_EXTENSIONS
MAX_FILE_SIZE = 10_000_000  # 10MB


def _strip_html(text: str) -> str:
    """Simple HTML tag removal without external dependencies."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _convert_html(src: Path, staging_dir: Path, root: Path) -> Path | None:
    """Convert an HTML file to .txt in the staging directory."""
    try:
        content = src.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    content = _strip_html(content)
    header = f"# {src.stem}\n\nSource: {src.relative_to(root)}\n\n"
    content = header + content

    rel = src.relative_to(root)
    out = staging_dir / rel.with_suffix(".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out


class MCPClient:
    """Minimal MCP client — speaks JSON-RPC 2.0 over stdin/stdout."""

    def __init__(self, command: list[str], env: dict[str, str], cwd: str | None = None) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            cwd=cwd,
        )
        self._id = 0

    def _send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg: dict = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        raw = json.dumps(msg, separators=(",", ":"))
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._proc.stdin.write((raw + "\n").encode())
        self._proc.stdin.flush()

        return self._read_response()

    def _read_response(self) -> dict:
        assert self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline().decode().strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def initialize(self) -> dict:
        return self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ingest-script", "version": "1.0"},
            },
        )

    def initialized(self) -> None:
        assert self._proc.stdin is not None
        msg = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            separators=(",", ":"),
        )
        self._proc.stdin.write((msg + "\n").encode())
        self._proc.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._send("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.terminate()
        self._proc.wait(timeout=10)


def main() -> None:  # noqa: PLR0912, PLR0915
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
    html_files = [f for f in all_files if f.suffix.lower() in HTML_EXTENSIONS]

    print(f"Found {len(all_files)} documentation files in {base_dir}")
    by_ext: dict[str, int] = {}
    for f in all_files:
        ext = f.suffix.lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1
    for ext in sorted(by_ext):
        print(f"  {ext}: {by_ext[ext]}")

    if not all_files:
        print("No documentation files found. Check your STERLING_DOCS_DIR path.")
        print(f"Supported: {', '.join(sorted(ALL_EXTENSIONS))}")
        sys.exit(1)

    # Convert HTML files to .txt (mcp-local-rag doesn't handle HTML)
    staging_dir = root / ".ingest-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    converted: list[Path] = []
    if html_files:
        print(f"\nConverting {len(html_files)} HTML files to .txt...")
        for f in html_files:
            out = _convert_html(f, staging_dir, root)
            if out:
                converted.append(out)
        print(f"  Converted {len(converted)} files to {staging_dir}")

    ingest_files = native_files + converted
    print(f"\nTotal files to ingest: {len(ingest_files)}")

    print("\nStarting mcp-local-rag server...")
    env = {**os.environ, "BASE_DIR": str(root)}
    client = MCPClient(["npx", "-y", "mcp-local-rag"], env=env, cwd=str(root))

    try:
        resp = client.initialize()
        if "error" in resp:
            print(f"MCP server initialization failed: {resp}")
            print("Is Node.js installed? Run: npx -y mcp-local-rag")
            sys.exit(1)
        client.initialized()
        print("MCP server ready.")

        print("\nIngesting documentation...")
        print("This may take a while for large doc sets (17K files = 10-24 hours).")
        print("The vector index persists — you only need to do this once.\n")

        ok = 0
        fail = 0
        for i, f in enumerate(ingest_files):
            try:
                result = client.call_tool("ingest_file", {"filePath": str(f)})
                if "error" in result:
                    fail += 1
                else:
                    ok += 1
            except Exception:
                fail += 1

            if (i + 1) % 50 == 0:
                print(f"  [{i + 1}/{len(ingest_files)}] {ok} ok, {fail} failed")

        print(f"\nDone: {ok} ingested, {fail} failed out of {len(ingest_files)}")
    finally:
        client.close()
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

    print(f"Ingestion complete. Vector index at: {root}/lancedb/")


if __name__ == "__main__":
    main()
