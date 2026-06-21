"""ContextCompressor seam — pluggable, opt-in read-side compression.

A provider seam (like ModelProvider / GovernanceProvider): compresses bulk tool/MCP
output before it re-enters an agent's context. OFF by default — enabled per workspace
via env (SWARMKIT_CONTEXT_COMPRESSION); the workspace.yaml `context_compression:` block
+ per-surface lossy/reversible policy are a later slice. Applied at the tool-output
boundary via the active-compressor module global (mirrors set_active_trace), so nothing
is threaded through the compiler. Never touches the audit log or inter-agent contract.

See design/details/context-compression.md.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

DEFAULT_MIN_BYTES = 2000

_OFF = {"", "off", "none", "0", "false", "no"}
_COLUMNAR = {"columnar", "builtin-columnar", "json", "on", "1", "true", "yes"}


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress one read-side payload. Must be lossless OR reversible (this tier is
    lossless). Returns the (possibly compressed) text; never raises into the run."""

    name: str

    def compress(self, text: str) -> str: ...


def build_compressor() -> ContextCompressor | None:
    """Resolve the configured compressor from env, or None (off — the default).

    SWARMKIT_CONTEXT_COMPRESSION: ``columnar`` (built-in lossless) | ``off`` (default).
    Unknown values resolve to None (off) — safe.
    """
    backend = os.environ.get("SWARMKIT_CONTEXT_COMPRESSION", "").strip().lower()
    if backend in _COLUMNAR:
        from swarmkit_runtime.compression._columnar import ColumnarCompressor  # noqa: PLC0415

        return ColumnarCompressor()
    return None


def _min_bytes() -> int:
    try:
        return int(os.environ.get("SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES", str(DEFAULT_MIN_BYTES)))
    except ValueError:
        return DEFAULT_MIN_BYTES


# Active compressor for the current run (set by WorkspaceRuntime.run, like set_active_trace).
_active: ContextCompressor | None = None


def set_active_compressor(compressor: ContextCompressor | None) -> None:
    global _active  # noqa: PLW0603
    _active = compressor


def get_active_compressor() -> ContextCompressor | None:
    return _active


def maybe_compress_tool_result(text: str) -> str:
    """Compress a tool/MCP result if a compressor is active and the payload is worth it.
    Never inflates, never raises — returns the original on any miss/error."""
    compressor = _active
    if compressor is None or not text or len(text) < _min_bytes():
        return text
    try:
        out = compressor.compress(text)
    except Exception:  # compression must never break a run
        return text
    if not out or len(out) >= len(text):
        return text  # no benefit (or inflated) — keep the original
    if os.environ.get("SWARMKIT_VERBOSE"):
        import sys  # noqa: PLC0415

        pct = 100 * (1 - len(out) / len(text))
        print(
            f"  [compress:{compressor.name}] {len(text)} -> {len(out)} chars ({pct:.0f}%)",
            file=sys.stderr,
        )
    return out
