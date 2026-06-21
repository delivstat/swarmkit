"""Built-in lossless compressor: minify + columnar-ize JSON tool output.

The bulk read-side win that's safe to apply: minify whitespace and rewrite
arrays-of-uniform-dicts into a `{columns, rows}` table (keys declared once instead of
per row). Information-preserving — a capable model reads the columnar form directly,
and it round-trips back to the same records. Non-JSON is left untouched (slice 1 is
JSON-only lossless; code/prose are later, lossy backends). Measured ~1.6x on Sterling's
consolidated CDT JSON. See design/details/context-compression.md.
"""

from __future__ import annotations

import json
from typing import Any

_MIN_ROWS = 3  # below this, columnar overhead isn't worth it


class ColumnarCompressor:
    """Lossless JSON minify + columnar rewrite."""

    name = "columnar"

    def compress(self, text: str) -> str:
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text  # not JSON — leave it (JSON-only in this tier)
        return json.dumps(_columnarize(obj), separators=(",", ":"), ensure_ascii=False)


def _columnarize(obj: Any) -> Any:
    """Recursively rewrite arrays-of-uniform-dicts to {columns, rows}; recurse elsewhere."""
    if isinstance(obj, list) and len(obj) >= _MIN_ROWS and all(isinstance(x, dict) for x in obj):
        keys: list[str] = []
        for row in obj:
            for k in row:
                if k not in keys:
                    keys.append(k)
        return {
            "columns": keys,
            "rows": [[_columnarize(row.get(k)) for k in keys] for row in obj],
        }
    if isinstance(obj, list):
        return [_columnarize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _columnarize(v) for k, v in obj.items()}
    return obj
