"""Ingest Sterling documentation into rag-mcp vector store.

Run once to build the vector index. Re-run when docs change.
Pure Python — no Node.js or MCP SDK dependency. Speaks JSON-RPC
(MCP protocol) directly over stdin/stdout to the rag-mcp process.

Ingests documentation files only: .md, .html, .pdf, .docx
Code files (.java, .xml, .xsl, .properties) belong on the
filesystem — the developer agent reads those directly.

Prerequisites:
    pip install rag-mcp   (or: uvx rag-mcp)

Usage:
    export STERLING_DOCS_DIR=~/sterling-knowledge/product-docs
    python scripts/ingest-docs.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".html", ".htm", ".pdf", ".docx"}
MAX_FILE_SIZE = 10_000_000  # 10MB


class MCPClient:
    """Minimal MCP client — speaks JSON-RPC 2.0 over stdin/stdout."""

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
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

        raw = json.dumps(msg)
        header = f"Content-Length: {len(raw)}\r\n\r\n"
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._proc.stdin.write(header.encode() + raw.encode())
        self._proc.stdin.flush()

        return self._read_response()

    def _read_response(self) -> dict:
        assert self._proc.stdout is not None
        content_length = 0
        while True:
            line = self._proc.stdout.readline().decode()
            if not line or line == "\r\n":
                break
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":")[1].strip())

        if content_length == 0:
            return {"error": "no response"}

        body = self._proc.stdout.read(content_length).decode()
        return json.loads(body)

    def initialize(self) -> dict:
        return self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ingest-script", "version": "1.0"},
        })

    def initialized(self) -> None:
        assert self._proc.stdin is not None
        msg = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        header = f"Content-Length: {len(msg)}\r\n\r\n"
        self._proc.stdin.write(header.encode() + msg.encode())
        self._proc.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._send("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.terminate()
        self._proc.wait(timeout=10)


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
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and f.stat().st_size < MAX_FILE_SIZE
        and "chroma" not in str(f)
        and "node_modules" not in str(f)
    ]

    print(f"Found {len(all_files)} documentation files in {base_dir}")
    by_ext: dict[str, int] = {}
    for f in all_files:
        ext = f.suffix.lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1
    for ext in sorted(by_ext):
        print(f"  {ext}: {by_ext[ext]}")

    if not all_files:
        print("No documentation files found. Check your STERLING_DOCS_DIR path.")
        print(f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    print("\nStarting rag-mcp server...")
    env = {**os.environ, "COLLECTION_NAME": Path(base_dir).name}
    client = MCPClient(["uvx", "rag-mcp"], env=env)

    try:
        resp = client.initialize()
        if "error" in resp:
            print(f"MCP server initialization failed: {resp}")
            print("Is rag-mcp installed? Run: pip install rag-mcp")
            sys.exit(1)
        client.initialized()
        print("MCP server ready.")

        print("\nIngesting documentation...")
        print("This may take a while for large doc sets.")
        print("The vector index persists — you only need to do this once.\n")

        ok = 0
        fail = 0
        for i, f in enumerate(all_files):
            try:
                result = client.call_tool(
                    "index_document", {"file_path": str(f)}
                )
                if "error" in result:
                    fail += 1
                else:
                    ok += 1
            except Exception:
                fail += 1

            if (i + 1) % 50 == 0:
                print(f"  [{i + 1}/{len(all_files)}] {ok} ok, {fail} failed")

        print(f"\nDone: {ok} ingested, {fail} failed out of {len(all_files)}")
    finally:
        client.close()

    print("Ingestion complete.")


if __name__ == "__main__":
    main()
