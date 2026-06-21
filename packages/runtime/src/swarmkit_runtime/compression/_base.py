"""ContextCompressor seam — pluggable, opt-in, per-surface read-side compression.

A provider seam (like ModelProvider / GovernanceProvider): compresses bulk tool/MCP
output before it re-enters an agent's context. OFF by default. Configured declaratively
per workspace via the ``context_compression:`` block (a default backend + per-surface
``overrides`` matched by tool-name glob), with env vars overriding the default per
deployment. Applied at the tool-output boundary via the active-policy module global
(mirrors set_active_trace), so nothing is threaded through the compiler. Never touches
the audit log or the inter-agent contract.

Two backend tiers:
  - lossless (``columnar``) — information-preserving; the agent reads the compact form.
  - reversible-lossy (``headtail``) — elides the middle but stashes the original in a
    per-run store keyed by a ref, recallable via the ``context_retrieve`` tool. Lossy at
    the point of read, but no information is destroyed — recall is deferred, governed, and
    audited.

See design/details/context-compression.md.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any, Protocol, runtime_checkable

DEFAULT_MIN_BYTES = 2000
_MAX_STASH = 256  # cap the per-run original store so a long run can't grow unbounded

# Env aliases. Empty string means "not set" → fall through to the workspace block.
_OFF_EXPLICIT = {"off", "none", "0", "false", "no"}
_COLUMNAR_ALIASES = {"columnar", "builtin-columnar", "json", "on", "1", "true", "yes"}


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress one read-side payload.

    Lossless backends return a self-contained compact form and ignore ``ref``.
    Reversible-lossy backends use ``ref`` to embed a recall marker; the seam stashes the
    original under that ref. Implementations must never raise into the run.
    """

    name: str
    reversible: bool

    def compress(self, text: str, ref: str | None = None) -> str: ...


def _make_compressor(name: str) -> ContextCompressor | None:
    """Instantiate a backend by name, or None for off/unknown (safe)."""
    n = (name or "").strip().lower()
    if not n or n in _OFF_EXPLICIT:
        return None
    if n in _COLUMNAR_ALIASES:
        n = "columnar"
    if n == "columnar":
        from swarmkit_runtime.compression._columnar import ColumnarCompressor  # noqa: PLC0415

        return ColumnarCompressor()
    if n == "headtail":
        from swarmkit_runtime.compression._headtail import HeadTailCompressor  # noqa: PLC0415

        return HeadTailCompressor()
    return None


# --- policy model -----------------------------------------------------------


@dataclass(frozen=True)
class CompressionRule:
    """Resolved compression decision for a surface: which backend + the size floor."""

    backend: str
    compressor: ContextCompressor | None
    min_bytes: int
    reversible: bool


@dataclass(frozen=True)
class CompressionPolicy:
    """A default rule plus tool-name-glob overrides. ``resolve`` picks the rule for a tool."""

    default: CompressionRule
    overrides: tuple[tuple[str, CompressionRule], ...] = ()

    def resolve(self, tool_name: str) -> CompressionRule:
        for glob, rule in self.overrides:
            if glob and fnmatch(tool_name, glob):
                return rule
        return self.default

    @property
    def any_reversible(self) -> bool:
        return self.default.reversible or any(r.reversible for _, r in self.overrides)


def _make_rule(backend_name: str, min_bytes: int) -> CompressionRule:
    comp = _make_compressor(backend_name)
    return CompressionRule(
        backend=comp.name if comp else "off",
        compressor=comp,
        min_bytes=min_bytes,
        reversible=bool(comp and getattr(comp, "reversible", False)),
    )


def _env_backend() -> str:
    return os.environ.get("SWARMKIT_CONTEXT_COMPRESSION", "").strip().lower()


def _env_min_bytes() -> int | None:
    raw = os.environ.get("SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES")
    if raw is not None and raw.strip():
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _cfg_backend(cfg: Any) -> str:
    if cfg is None:
        return ""
    backend = getattr(cfg, "backend", None)
    value = getattr(backend, "value", backend)  # pydantic Enum -> str
    return value.strip().lower() if isinstance(value, str) else ""


def _cfg_min_bytes(cfg: Any) -> int | None:
    mb = getattr(cfg, "min_bytes", None) if cfg is not None else None
    return mb if isinstance(mb, int) else None


def _cfg_overrides(cfg: Any) -> list[Any]:
    ov = getattr(cfg, "overrides", None) if cfg is not None else None
    return list(ov) if isinstance(ov, list) else []


def build_policy(workspace_cfg: Any = None) -> CompressionPolicy | None:
    """Resolve the effective compression policy, or None (off — the default).

    Precedence for the default rule: ``SWARMKIT_CONTEXT_COMPRESSION`` /
    ``…_MIN_BYTES`` env vars, then the workspace ``context_compression`` block, then off /
    2000. An explicit env ``off`` disables compression entirely (including overrides).
    Per-surface ``overrides`` come from the workspace block only.
    """
    env_b = _env_backend()
    if env_b in _OFF_EXPLICIT:
        return None  # operator force-off

    default_backend = env_b or _cfg_backend(workspace_cfg) or "off"
    default_min = _env_min_bytes()
    if default_min is None:
        default_min = _cfg_min_bytes(workspace_cfg)
    if default_min is None:
        default_min = DEFAULT_MIN_BYTES
    default_rule = _make_rule(default_backend, default_min)

    overrides: list[tuple[str, CompressionRule]] = []
    for ov in _cfg_overrides(workspace_cfg):
        match = getattr(ov, "match", None)
        if not isinstance(match, str) or not match:
            continue
        ov_backend = _cfg_backend(ov) or default_backend
        ov_min = _cfg_min_bytes(ov)
        if ov_min is None:
            ov_min = default_min
        overrides.append((match, _make_rule(ov_backend, ov_min)))

    if default_rule.compressor is None and not any(r.compressor for _, r in overrides):
        return None  # nothing to compress — fast no-op
    return CompressionPolicy(default=default_rule, overrides=tuple(overrides))


# --- active policy + per-run original store ---------------------------------

_active_policy: CompressionPolicy | None = None
_original_store: dict[str, str] = {}
_ref_counter: int = 0


def set_active_policy(policy: CompressionPolicy | None) -> None:
    """Install (or clear) the policy for the current run and reset the original store."""
    global _active_policy, _ref_counter  # noqa: PLW0603
    _active_policy = policy
    _ref_counter = 0
    _original_store.clear()


def get_active_policy() -> CompressionPolicy | None:
    return _active_policy


def get_original(ref: str) -> str | None:
    """Return the stashed pre-compression original for a ref, or None if unknown/expired."""
    return _original_store.get(ref)


def _make_ref(tool_name: str) -> str:
    global _ref_counter  # noqa: PLW0603
    _ref_counter += 1
    safe = re.sub(r"[^a-z0-9_-]", "-", (tool_name or "ctx").lower())[:40]
    return f"{safe}-{_ref_counter}"


def _stash_original(ref: str, text: str) -> None:
    _original_store[ref] = text
    if len(_original_store) > _MAX_STASH:
        # drop oldest (dict preserves insertion order)
        for key in list(_original_store)[: len(_original_store) - _MAX_STASH]:
            del _original_store[key]


def _record_compression(tool_name: str, backend: str, bytes_in: int, bytes_out: int) -> None:
    """Best-effort: record savings into the active trace + OTel. Never raises."""
    try:
        from swarmkit_runtime.langgraph_compiler._compiler import _active_trace  # noqa: PLC0415

        if _active_trace is not None:
            _active_trace.record_compression(tool_name, backend, bytes_in, bytes_out)
    except Exception:  # telemetry must never break a run
        pass
    try:
        from swarmkit_runtime.telemetry import record_compression as _rec  # noqa: PLC0415

        _rec(tool_name=tool_name, backend=backend, bytes_in=bytes_in, bytes_out=bytes_out)
    except Exception:
        pass


def maybe_compress_tool_result(text: str, tool_name: str = "") -> str:
    """Compress a tool/MCP result per the active policy if the payload is worth it.

    Resolves the per-surface rule for ``tool_name``. For reversible backends, stashes the
    original under a fresh ref so ``context_retrieve`` can recall it. Never inflates, never
    raises — returns the original on any miss/error.
    """
    policy = _active_policy
    if policy is None or not text:
        return text
    rule = policy.resolve(tool_name)
    comp = rule.compressor
    if comp is None or len(text) < rule.min_bytes:
        return text

    ref: str | None = None
    try:
        if rule.reversible:
            ref = _make_ref(tool_name)
            out = comp.compress(text, ref)
        else:
            out = comp.compress(text)
    except Exception:  # compression must never break a run
        return text
    if not out or len(out) >= len(text):
        return text  # no benefit (or inflated) — keep the original

    if rule.reversible and ref is not None:
        _stash_original(ref, text)
    _record_compression(tool_name, rule.backend, len(text), len(out))

    if os.environ.get("SWARMKIT_VERBOSE"):
        import sys  # noqa: PLC0415

        pct = 100 * (1 - len(out) / len(text))
        surface = f" {tool_name}" if tool_name else ""
        print(
            f"  [compress:{rule.backend}{surface}] {len(text)} -> {len(out)} chars ({pct:.0f}%)",
            file=sys.stderr,
        )
    return out
