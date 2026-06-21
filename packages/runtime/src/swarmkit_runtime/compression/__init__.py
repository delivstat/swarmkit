"""Context compression — opt-in, lossless read-side compression of tool output.

See design/details/context-compression.md. Off by default; enable per workspace via
``SWARMKIT_CONTEXT_COMPRESSION=columnar``.
"""

from __future__ import annotations

from swarmkit_runtime.compression._base import (
    ContextCompressor,
    build_compressor,
    get_active_compressor,
    maybe_compress_tool_result,
    set_active_compressor,
)
from swarmkit_runtime.compression._columnar import ColumnarCompressor

__all__ = [
    "ColumnarCompressor",
    "ContextCompressor",
    "build_compressor",
    "get_active_compressor",
    "maybe_compress_tool_result",
    "set_active_compressor",
]
