"""Tests for the context-compression seam.

Covers the lossless columnar backend, the reversible-lossy headtail backend, the
per-surface policy (default + tool-name-glob overrides), the active-policy gate, and the
per-run original store backing context_retrieve. The seam must never inflate, never raise
into a run, and stay off unless explicitly enabled.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from swarmkit_runtime.compression import (
    ColumnarCompressor,
    CompressionPolicy,
    CompressionRule,
    HeadTailCompressor,
    build_policy,
    get_active_policy,
    get_original,
    maybe_compress_tool_result,
    set_active_policy,
)
from swarmkit_runtime.langgraph_compiler._prompts import _build_tools
from swarmkit_runtime.langgraph_compiler._tool_loop import _handle_context_retrieve


@pytest.fixture(autouse=True)
def _reset_active() -> Iterator[None]:
    """Each test starts with no active policy and clears it afterwards."""
    set_active_policy(None)
    yield
    set_active_policy(None)


# Stand-ins for the generated pydantic models (build_policy is duck-typed via getattr;
# the real-model path is covered by the schema fixture round-trip tests). Avoids
# pydantic-plugin friction under mypy --strict.
class _Backend:
    def __init__(self, value: str) -> None:
        self.value = value


class CompressionOverride:
    def __init__(
        self, match: str, backend: str | None = None, min_bytes: int | None = None
    ) -> None:
        self.match = match
        self.backend = _Backend(backend) if backend is not None else None
        self.min_bytes = min_bytes


class ContextCompression:
    def __init__(
        self,
        backend: str | None = None,
        min_bytes: int | None = None,
        overrides: list[CompressionOverride] | None = None,
    ) -> None:
        self.backend = _Backend(backend) if backend is not None else None
        self.min_bytes = min_bytes
        self.overrides = overrides


# --- ColumnarCompressor: losslessness ---------------------------------------


def _roundtrip(columnar: dict[str, object]) -> list[dict[str, object]]:
    cols = columnar["columns"]
    rows = columnar["rows"]
    assert isinstance(cols, list)
    assert isinstance(rows, list)
    return [dict(zip(cols, row, strict=True)) for row in rows]


def test_columnar_rewrites_array_of_dicts_losslessly() -> None:
    rows = [{"id": i, "name": f"r{i}", "qty": i * 10} for i in range(3)]
    out = ColumnarCompressor().compress(json.dumps(rows))
    obj = json.loads(out)
    assert obj["columns"] == ["id", "name", "qty"]
    assert _roundtrip(obj) == rows


def test_columnar_passes_through_non_json() -> None:
    text = "this is just prose, not json at all"
    assert ColumnarCompressor().compress(text) == text


def test_columnar_minifies_whitespace() -> None:
    pretty = json.dumps({"a": 1, "b": 2}, indent=2)
    out = ColumnarCompressor().compress(pretty)
    assert "\n" not in out
    assert json.loads(out) == {"a": 1, "b": 2}


def test_columnar_ignores_ref_arg() -> None:
    rows = [{"id": i} for i in range(3)]
    assert ColumnarCompressor().compress(
        json.dumps(rows), "some-ref"
    ) == ColumnarCompressor().compress(json.dumps(rows))


# --- HeadTailCompressor: reversible-lossy -----------------------------------


def test_headtail_keeps_head_and_tail_elides_middle() -> None:
    c = HeadTailCompressor(head=20, tail=10)
    text = "H" * 20 + "M" * 500 + "T" * 10
    out = c.compress(text, ref="logs-1")
    assert out.startswith("H" * 20)
    assert out.endswith("T" * 10)
    assert "M" * 500 not in out
    assert "logs-1" in out
    assert "elided" in out
    assert len(out) < len(text)


def test_headtail_short_text_unchanged() -> None:
    c = HeadTailCompressor(head=20, tail=10)
    text = "short"
    assert c.compress(text, ref="x") == text


def test_headtail_is_reversible_flag() -> None:
    assert HeadTailCompressor().reversible is True
    assert ColumnarCompressor().reversible is False


# --- build_policy: env + workspace resolution -------------------------------


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTEXT_COMPRESSION", raising=False)
    assert build_policy(None) is None


def test_env_enables_columnar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", "columnar")
    policy = build_policy(None)
    assert policy is not None
    assert policy.default.backend == "columnar"


def test_env_off_disables_even_with_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", "off")
    cfg = ContextCompression(backend="columnar")
    assert build_policy(cfg) is None


def test_workspace_block_columnar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTEXT_COMPRESSION", raising=False)
    policy = build_policy(ContextCompression(backend="columnar", min_bytes=500))
    assert policy is not None
    assert policy.default.backend == "columnar"
    assert policy.default.min_bytes == 500


def test_env_overrides_workspace_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", "columnar")
    policy = build_policy(ContextCompression(backend="off"))
    assert policy is not None
    assert policy.default.backend == "columnar"


def test_min_bytes_env_overrides_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", "columnar")
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES", "9999")
    policy = build_policy(ContextCompression(backend="columnar", min_bytes=10))
    assert policy is not None
    assert policy.default.min_bytes == 9999


def test_per_surface_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTEXT_COMPRESSION", raising=False)
    cfg = ContextCompression(
        backend="columnar",
        min_bytes=2000,
        overrides=[
            CompressionOverride(match="get-logs*", backend="headtail", min_bytes=100),
            CompressionOverride(match="search-*", backend="off"),
        ],
    )
    policy = build_policy(cfg)
    assert policy is not None
    assert policy.resolve("get-logs-today").backend == "headtail"
    assert policy.resolve("get-logs-today").min_bytes == 100
    assert policy.resolve("search-docs").compressor is None  # off override
    assert policy.resolve("anything-else").backend == "columnar"  # default
    assert policy.any_reversible is True


def test_policy_none_when_all_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTEXT_COMPRESSION", raising=False)
    cfg = ContextCompression(
        backend="off", overrides=[CompressionOverride(match="x", backend="off")]
    )
    assert build_policy(cfg) is None


# --- maybe_compress_tool_result: the gate -----------------------------------


def _big_json_array() -> str:
    return json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)], indent=2)


def _activate(
    backend: str, min_bytes: int = 0, overrides: list[CompressionOverride] | None = None
) -> None:
    policy = build_policy(
        ContextCompression(backend=backend, min_bytes=min_bytes, overrides=overrides)
    )
    set_active_policy(policy)


def test_gate_noop_when_no_policy() -> None:
    text = _big_json_array()
    assert maybe_compress_tool_result(text) == text


def test_gate_compresses_columnar_when_active() -> None:
    _activate("columnar")
    text = _big_json_array()
    out = maybe_compress_tool_result(text, "get-data")
    assert len(out) < len(text)
    assert "columns" in out


def test_gate_skips_below_min_bytes() -> None:
    _activate("columnar", min_bytes=10_000_000)
    text = _big_json_array()
    assert maybe_compress_tool_result(text, "get-data") == text


def test_gate_never_inflates() -> None:
    _activate("columnar")
    text = json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]) + " " * 3000
    out = maybe_compress_tool_result(text, "x")
    assert len(out) <= len(text)


def test_gate_never_raises_on_broken_backend() -> None:
    class _Boom:
        name = "boom"
        reversible = False

        def compress(self, text: str, ref: str | None = None) -> str:
            raise RuntimeError("kaboom")

    rule = CompressionRule(backend="boom", compressor=_Boom(), min_bytes=0, reversible=False)
    set_active_policy(CompressionPolicy(default=rule))
    text = _big_json_array()
    assert maybe_compress_tool_result(text, "x") == text


def test_gate_handles_empty_text() -> None:
    _activate("columnar")
    assert maybe_compress_tool_result("") == ""


def test_per_surface_routing_in_gate() -> None:
    _activate(
        "off",
        overrides=[CompressionOverride(match="get-logs", backend="headtail", min_bytes=0)],
    )
    big_log = "L" * 50_000
    # default is off → non-matching tool unchanged
    assert maybe_compress_tool_result(big_log, "other-tool") == big_log
    # matching tool → headtail elides
    out = maybe_compress_tool_result(big_log, "get-logs")
    assert len(out) < len(big_log)
    assert "elided" in out


# --- reversible store backing context_retrieve ------------------------------


def test_headtail_stashes_original_for_retrieve() -> None:
    _activate("headtail", min_bytes=0)
    original = "A" * 100 + "B" * 50_000 + "C" * 100
    out = maybe_compress_tool_result(original, "get-logs")
    assert len(out) < len(original)
    # extract the ref from the marker
    assert 'ref="' in out
    ref = out.split('ref="', 1)[1].split('"', 1)[0]
    assert get_original(ref) == original


def test_get_original_unknown_ref_is_none() -> None:
    assert get_original("nope-1") is None


def test_set_active_policy_resets_store() -> None:
    _activate("headtail", min_bytes=0)
    original = "X" * 60_000
    out = maybe_compress_tool_result(original, "get-logs")
    ref = out.split('ref="', 1)[1].split('"', 1)[0]
    assert get_original(ref) is not None
    set_active_policy(None)  # new run
    assert get_original(ref) is None


def test_get_active_policy() -> None:
    assert get_active_policy() is None
    _activate("columnar")
    assert get_active_policy() is not None


# --- context_retrieve tool handler ------------------------------------------


def _block(**tool_input: object) -> object:
    return SimpleNamespace(
        tool_name="context_retrieve", tool_use_id="call_0", tool_input=dict(tool_input)
    )


@pytest.mark.asyncio
async def test_context_retrieve_returns_window() -> None:
    _activate("headtail", min_bytes=0)
    original = "A" * 100 + "B" * 50_000 + "C" * 100
    out = maybe_compress_tool_result(original, "get-logs")
    ref = out.split('ref="', 1)[1].split('"', 1)[0]

    res = await _handle_context_retrieve(_block(ref=ref, offset=0, limit=120), None, "agent-1")
    assert f"ref={ref}" in res
    assert "A" * 100 in res
    assert "more chars" in res  # paging hint, since limit < len


@pytest.mark.asyncio
async def test_context_retrieve_unknown_ref() -> None:
    res = await _handle_context_retrieve(_block(ref="missing-1"), None, "agent-1")
    assert "no stashed content" in res


@pytest.mark.asyncio
async def test_context_retrieve_requires_ref() -> None:
    res = await _handle_context_retrieve(_block(), None, "agent-1")
    assert "'ref' is required" in res


# --- context_retrieve tool injection in _build_tools ------------------------


def _bare_agent() -> Any:
    return SimpleNamespace(skills=[], children=[])


def test_retrieve_tool_offered_only_when_reversible() -> None:
    # lossless-only policy → no retrieve tool
    _activate("columnar")
    names = {t.name for t in _build_tools(_bare_agent())}
    assert "context_retrieve" not in names

    # reversible policy → retrieve tool offered
    _activate("headtail")
    names = {t.name for t in _build_tools(_bare_agent())}
    assert "context_retrieve" in names

    # no policy → no retrieve tool
    set_active_policy(None)
    names = {t.name for t in _build_tools(_bare_agent())}
    assert "context_retrieve" not in names
