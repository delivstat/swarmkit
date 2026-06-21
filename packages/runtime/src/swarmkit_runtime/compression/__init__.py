"""Context compression — opt-in, per-surface read-side compression of tool output.

See design/details/context-compression.md. Off by default; enable per workspace via the
``context_compression:`` block (default backend + tool-name-glob ``overrides``) or env
``SWARMKIT_CONTEXT_COMPRESSION``. Lossless (``columnar``) and reversible-lossy
(``headtail`` + ``context_retrieve``) backends.
"""

from __future__ import annotations

from swarmkit_runtime.compression._base import (
    CompressionPolicy,
    CompressionRule,
    ContextCompressor,
    build_policy,
    get_active_policy,
    get_original,
    maybe_compress_tool_result,
    set_active_policy,
)
from swarmkit_runtime.compression._columnar import ColumnarCompressor
from swarmkit_runtime.compression._headtail import HeadTailCompressor

__all__ = [
    "ColumnarCompressor",
    "CompressionPolicy",
    "CompressionRule",
    "ContextCompressor",
    "HeadTailCompressor",
    "build_policy",
    "get_active_policy",
    "get_original",
    "maybe_compress_tool_result",
    "set_active_policy",
]
