"""Ingest Sterling documentation into mcp-local-rag.

Run once to build the vector index. Re-run when docs change.

Usage:
    export STERLING_DOCS_DIR=~/sterling-knowledge
    uv run python scripts/ingest-docs.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession


EXTENSIONS = {".html", ".htm", ".md", ".txt", ".xml", ".properties", ".java", ".pdf", ".docx"}
MAX_FILE_SIZE = 10_000_000  # 10MB


async def main() -> None:
    base_dir = os.environ.get("STERLING_DOCS_DIR", os.path.expanduser("~/sterling-knowledge"))

    params = StdioServerParameters(
        command="npx",
        args=["-y", "mcp-local-rag"],
        env={**os.environ, "BASE_DIR": base_dir},
    )

    async with stdio_client(params) as transport:
        async with ClientSession(*transport) as session:
            await session.initialize()

            result = await session.call_tool("status", {})
            for block in result.content:
                print(getattr(block, "text", ""))

            root = Path(base_dir)
            files = [
                f
                for f in root.rglob("*")
                if f.is_file()
                and f.suffix.lower() in EXTENSIONS
                and f.stat().st_size < MAX_FILE_SIZE
            ]

            print(f"\nFound {len(files)} files to ingest.")

            for i, file_path in enumerate(files, 1):
                rel = file_path.relative_to(root)
                try:
                    result = await session.call_tool(
                        "ingest_file", {"filePath": str(file_path)}
                    )
                    status = "ok"
                    for block in result.content:
                        text = getattr(block, "text", "")
                        if "error" in text.lower():
                            status = "error"
                except Exception as e:
                    status = f"failed: {e}"

                if i % 50 == 0 or status != "ok":
                    print(f"  [{i}/{len(files)}] {rel} — {status}")

            print("\nIngestion complete.")
            result = await session.call_tool("status", {})
            for block in result.content:
                print(getattr(block, "text", ""))


if __name__ == "__main__":
    asyncio.run(main())
