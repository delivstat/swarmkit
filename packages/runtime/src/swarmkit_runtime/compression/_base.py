"""ContextCompressor seam — pluggable, opt-in read-side compression.

A provider seam (like ModelProvider / GovernanceProvider): compresses bulk tool/MCP
output before it re-enters an agent's context. OFF by default — enabled either
declaratively per workspace via the ``context_compression:`` block in workspace.yaml
or via env (``SWARMKIT_CONTEXT_COMPRESSION`` / ``SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES``),
which override the workspace block per deployment. Applied at the tool-output boundary via
the active-compressor module global (mirrors set_active_trace), so nothing is threaded
through the compiler. Never touches the audit log or inter-agent contract.

See design/details/context-compression.md.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

DEFAULT_MIN_BYTES = 2000

_OFF = {"", "off", "none", "0", "false", "no"}
_COLUMNAR = {"columnar", "builtin-columnar", "json", "on", "1", "true", "yes"}


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress one read-side payload. Must be lossless OR reversible (this tier is
    lossless). Returns the (possibly compressed) text; never raises into the run."""

    name: str

    def compress(self, text: str) -> str: ...


def _resolve_backend(workspace_cfg: Any = None) -> str:
    """Effective backend string: env override first, then the workspace block, then off."""
    env = os.environ.get("SWARMKIT_CONTEXT_COMPRESSION", "").strip().lower()
    if env:
        return env
    if workspace_cfg is not None:
        backend = getattr(workspace_cfg, "backend", None)
        # The pydantic model exposes an Enum; .value is the YAML string.
        value = getattr(backend, "value", backend)
        if isinstance(value, str):
            return value.strip().lower()
    return ""


def build_compressor(workspace_cfg: Any = None) -> ContextCompressor | None:
    """Resolve the configured compressor, or None (off — the default).

    Precedence: ``SWARMKIT_CONTEXT_COMPRESSION`` env var, then the workspace
    ``context_compression.backend`` field, then off. ``columnar`` selects the built-in
    lossless backend; any unknown value resolves to None (off) — safe.
    """
    if _resolve_backend(workspace_cfg) in _COLUMNAR:
        from swarmkit_runtime.compression._columnar import ColumnarCompressor  # noqa: PLC0415

        return ColumnarCompressor()
    return None


def resolve_min_bytes(workspace_cfg: Any = None) -> int:
    """Effective min-bytes threshold: env override first, then the workspace block, then default."""
    env = os.environ.get("SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES")
    if env is not None and env.strip():
        try:
            return int(env)
        except ValueError:
            pass
    if workspace_cfg is not None:
        mb = getattr(workspace_cfg, "min_bytes", None)
        if isinstance(mb, int):
            return mb
    return DEFAULT_MIN_BYTES


# Active compression state for the current run (set by WorkspaceRuntime.run, like
# set_active_trace). _active_min_bytes is None when unset → fall back to the env/default.
_active: ContextCompressor | None = None
_active_min_bytes: int | None = None


def set_active_compressor(compressor: ContextCompressor | None) -> None:
    global _active  # noqa: PLW0603
    _active = compressor


def get_active_compressor() -> ContextCompressor | None:
    return _active


def set_active_min_bytes(min_bytes: int | None) -> None:
    global _active_min_bytes  # noqa: PLW0603
    _active_min_bytes = min_bytes


def _effective_min_bytes() -> int:
    if _active_min_bytes is not None:
        return _active_min_bytes
    return resolve_min_bytes()


def maybe_compress_tool_result(text: str) -> str:
    """Compress a tool/MCP result if a compressor is active and the payload is worth it.
    Never inflates, never raises — returns the original on any miss/error."""
    compressor = _active
    if compressor is None or not text or len(text) < _effective_min_bytes():
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
