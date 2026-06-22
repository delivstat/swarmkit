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
from contextvars import ContextVar
from dataclasses import dataclass, field
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


def _load_plugin_compressor(class_path: str) -> ContextCompressor | None:
    """Import and instantiate a custom backend from a fully-qualified class path.

    The pluggable seam: third-party / learned backends register without a runtime edit,
    same shape as model_providers' class-path config. Returns None (safe-off) on any
    import/instantiation failure or if the result isn't a ContextCompressor.
    """
    if not class_path or "." not in class_path:
        return None
    import importlib  # noqa: PLC0415

    module_path, _, cls_name = class_path.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        instance = cls()
    except Exception:  # a bad plugin must not break a run; resolve to off
        return None
    if not isinstance(instance, ContextCompressor):
        return None
    return instance


def _make_compressor(name: str, backend_class: str = "") -> ContextCompressor | None:
    """Instantiate a backend by name (or class path for ``plugin``), or None for off/unknown."""
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
    if n == "plugin":
        return _load_plugin_compressor(backend_class)
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
class SurfaceOverride:
    """A per-surface rule plus the globs that select it (by tool name and/or server id)."""

    tool_glob: str
    server_glob: str
    rule: CompressionRule

    def matches(self, tool_name: str, server_id: str) -> bool:
        if self.tool_glob and fnmatch(tool_name, self.tool_glob):
            return True
        return bool(self.server_glob and server_id and fnmatch(server_id, self.server_glob))


@dataclass(frozen=True)
class CompressionPolicy:
    """A default rule plus per-surface overrides. ``resolve`` picks the rule for a call."""

    default: CompressionRule
    overrides: tuple[SurfaceOverride, ...] = ()

    def resolve(self, tool_name: str, server_id: str = "") -> CompressionRule:
        for ov in self.overrides:
            if ov.matches(tool_name, server_id):
                return ov.rule
        return self.default

    @property
    def any_reversible(self) -> bool:
        return self.default.reversible or any(ov.rule.reversible for ov in self.overrides)


def _make_rule(backend_name: str, min_bytes: int, backend_class: str = "") -> CompressionRule:
    comp = _make_compressor(backend_name, backend_class)
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


def _cfg_str(cfg: Any, attr: str) -> str:
    val = getattr(cfg, attr, None) if cfg is not None else None
    return val.strip() if isinstance(val, str) else ""


def _cfg_overrides(cfg: Any) -> list[Any]:
    ov = getattr(cfg, "overrides", None) if cfg is not None else None
    return list(ov) if isinstance(ov, list) else []


def build_policy(workspace_cfg: Any = None) -> CompressionPolicy | None:
    """Resolve the effective compression policy, or None (off — the default).

    Precedence for the default rule: ``SWARMKIT_CONTEXT_COMPRESSION`` /
    ``…_MIN_BYTES`` env vars, then the workspace ``context_compression`` block, then off /
    2000. An explicit env ``off`` disables compression entirely (including overrides).
    Per-surface ``overrides`` (matched by tool-name and/or server-id glob) come from the
    workspace block only.
    """
    env_b = _env_backend()
    if env_b in _OFF_EXPLICIT:
        return None  # operator force-off

    default_backend = env_b or _cfg_backend(workspace_cfg) or "off"
    default_class = _cfg_str(workspace_cfg, "backend_class")
    default_min = _env_min_bytes()
    if default_min is None:
        default_min = _cfg_min_bytes(workspace_cfg)
    if default_min is None:
        default_min = DEFAULT_MIN_BYTES
    default_rule = _make_rule(default_backend, default_min, default_class)

    overrides: list[SurfaceOverride] = []
    for ov in _cfg_overrides(workspace_cfg):
        tool_glob = _cfg_str(ov, "match")
        server_glob = _cfg_str(ov, "match_server")
        if not tool_glob and not server_glob:
            continue
        ov_backend = _cfg_backend(ov) or default_backend
        ov_class = _cfg_str(ov, "backend_class") or default_class
        ov_min = _cfg_min_bytes(ov)
        if ov_min is None:
            ov_min = default_min
        overrides.append(
            SurfaceOverride(
                tool_glob=tool_glob,
                server_glob=server_glob,
                rule=_make_rule(ov_backend, ov_min, ov_class),
            )
        )

    if default_rule.compressor is None and not any(ov.rule.compressor for ov in overrides):
        return None  # nothing to compress — fast no-op
    return CompressionPolicy(default=default_rule, overrides=tuple(overrides))


# --- active policy + per-run original store ---------------------------------


@dataclass
class _RunState:
    """Per-run compression state: the policy + the reversible original store + ref counter."""

    policy: CompressionPolicy | None
    store: dict[str, str] = field(default_factory=dict)
    counter: int = 0


# ContextVar (not a module global): asyncio copies the context when a task is created, so
# concurrent runs in one process — e.g. jobs under `swarmkit serve` — each see their own
# policy and original store instead of clobbering a shared global. Mirrors _active_trace.
_run_state_var: ContextVar[_RunState | None] = ContextVar(
    "swarmkit_compression_state", default=None
)


def set_active_policy(policy: CompressionPolicy | None) -> None:
    """Install (or clear) the policy for the current run context, with a fresh original store."""
    _run_state_var.set(_RunState(policy=policy) if policy is not None else None)


def get_active_policy() -> CompressionPolicy | None:
    state = _run_state_var.get()
    return state.policy if state is not None else None


def get_original(ref: str) -> str | None:
    """Return the stashed pre-compression original for a ref, or None if unknown/expired."""
    state = _run_state_var.get()
    return state.store.get(ref) if state is not None else None


def _next_ref(state: _RunState, tool_name: str) -> str:
    state.counter += 1
    safe = re.sub(r"[^a-z0-9_-]", "-", (tool_name or "ctx").lower())[:40]
    return f"{safe}-{state.counter}"


def _stash_original(state: _RunState, ref: str, text: str) -> None:
    state.store[ref] = text
    if len(state.store) > _MAX_STASH:
        # drop oldest (dict preserves insertion order)
        for key in list(state.store)[: len(state.store) - _MAX_STASH]:
            del state.store[key]


def _record_compression(tool_name: str, backend: str, bytes_in: int, bytes_out: int) -> None:
    """Best-effort: record savings into the active trace + OTel. Never raises."""
    try:
        from swarmkit_runtime.langgraph_compiler._compiler import (  # noqa: PLC0415
            get_active_trace,
        )

        trace = get_active_trace()
        if trace is not None:
            trace.record_compression(tool_name, backend, bytes_in, bytes_out)
    except Exception:  # telemetry must never break a run
        pass
    try:
        from swarmkit_runtime.telemetry import record_compression as _rec  # noqa: PLC0415

        _rec(tool_name=tool_name, backend=backend, bytes_in=bytes_in, bytes_out=bytes_out)
    except Exception:
        pass


def maybe_compress_tool_result(text: str, tool_name: str = "", server_id: str = "") -> str:
    """Compress a tool/MCP result per the active policy if the payload is worth it.

    Resolves the per-surface rule for ``tool_name`` / ``server_id``. For reversible backends,
    stashes the original under a fresh ref so ``context_retrieve`` can recall it. Never
    inflates, never raises — returns the original on any miss/error.
    """
    state = _run_state_var.get()
    if state is None or state.policy is None or not text:
        return text
    rule = state.policy.resolve(tool_name, server_id)
    comp = rule.compressor
    if comp is None or len(text) < rule.min_bytes:
        return text

    ref: str | None = None
    try:
        if rule.reversible:
            ref = _next_ref(state, tool_name)
            out = comp.compress(text, ref)
        else:
            out = comp.compress(text)
    except Exception:  # compression must never break a run
        return text
    if not out or len(out) >= len(text):
        return text  # no benefit (or inflated) — keep the original

    if rule.reversible and ref is not None:
        _stash_original(state, ref, text)
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
