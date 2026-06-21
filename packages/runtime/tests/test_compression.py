"""Tests for the opt-in context-compression seam.

Covers the lossless columnar compressor and the active-compressor gate used at the
tool-output boundary. The seam must never inflate, never raise into a run, and stay
off unless explicitly enabled.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from swarmkit_runtime.compression import (
    ColumnarCompressor,
    build_compressor,
    get_active_compressor,
    maybe_compress_tool_result,
    set_active_compressor,
)


@pytest.fixture(autouse=True)
def _reset_active() -> Iterator[None]:
    """Each test starts with no active compressor and clears it afterwards."""
    set_active_compressor(None)
    yield
    set_active_compressor(None)


# --- ColumnarCompressor: losslessness ---------------------------------------


def _roundtrip(columnar: dict[str, object]) -> list[dict[str, object]]:
    """Reconstruct the original records from a {columns, rows} table."""
    cols = columnar["columns"]
    rows = columnar["rows"]
    assert isinstance(cols, list)
    assert isinstance(rows, list)
    return [dict(zip(cols, row, strict=True)) for row in rows]


def test_columnar_rewrites_array_of_dicts_losslessly() -> None:
    rows = [
        {"id": 1, "name": "a", "qty": 10},
        {"id": 2, "name": "b", "qty": 20},
        {"id": 3, "name": "c", "qty": 30},
    ]
    out = ColumnarCompressor().compress(json.dumps(rows))
    obj = json.loads(out)
    assert obj["columns"] == ["id", "name", "qty"]
    assert _roundtrip(obj) == rows


def test_columnar_handles_ragged_rows() -> None:
    rows = [
        {"id": 1, "name": "a"},
        {"id": 2, "qty": 20},
        {"id": 3, "name": "c", "qty": 30},
    ]
    out = json.loads(ColumnarCompressor().compress(json.dumps(rows)))
    # union of keys, missing values become null and round-trip back to absent-as-None
    assert set(out["columns"]) == {"id", "name", "qty"}
    reconstructed = _roundtrip(out)
    assert reconstructed[0]["id"] == 1
    assert reconstructed[1]["name"] is None
    assert reconstructed[1]["qty"] == 20


def test_columnar_recurses_into_nested_arrays() -> None:
    payload = {
        "results": [
            {"k": 1, "tags": ["x", "y"]},
            {"k": 2, "tags": ["z"]},
            {"k": 3, "tags": []},
        ]
    }
    out = json.loads(ColumnarCompressor().compress(json.dumps(payload)))
    assert out["results"]["columns"] == ["k", "tags"]
    assert _roundtrip(out["results"])[0]["tags"] == ["x", "y"]


def test_columnar_leaves_small_arrays_alone() -> None:
    rows = [{"id": 1}, {"id": 2}]  # below _MIN_ROWS
    out = json.loads(ColumnarCompressor().compress(json.dumps(rows)))
    assert out == rows


def test_columnar_passes_through_non_json() -> None:
    text = "this is just prose, not json at all"
    assert ColumnarCompressor().compress(text) == text


def test_columnar_minifies_whitespace() -> None:
    pretty = json.dumps({"a": 1, "b": 2}, indent=2)
    out = ColumnarCompressor().compress(pretty)
    assert "\n" not in out
    assert len(out) < len(pretty)
    assert json.loads(out) == {"a": 1, "b": 2}


# --- build_compressor: env config -------------------------------------------


def test_build_compressor_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTEXT_COMPRESSION", raising=False)
    assert build_compressor() is None


@pytest.mark.parametrize("value", ["columnar", "on", "1", "true", "json", "COLUMNAR"])
def test_build_compressor_enables_columnar(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", value)
    compressor = build_compressor()
    assert isinstance(compressor, ColumnarCompressor)


@pytest.mark.parametrize("value", ["off", "none", "0", "false", "", "garbage"])
def test_build_compressor_disabled_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION", value)
    assert build_compressor() is None


# --- maybe_compress_tool_result: the gate -----------------------------------


def _big_json_array() -> str:
    return json.dumps([{"id": i, "name": f"row-{i}"} for i in range(200)], indent=2)


def test_gate_noop_when_no_active_compressor() -> None:
    text = _big_json_array()
    assert maybe_compress_tool_result(text) == text


def test_gate_compresses_when_active() -> None:
    set_active_compressor(ColumnarCompressor())
    text = _big_json_array()
    out = maybe_compress_tool_result(text)
    assert len(out) < len(text)
    assert "columns" in out


def test_gate_skips_small_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES", "100000")
    set_active_compressor(ColumnarCompressor())
    text = _big_json_array()
    assert maybe_compress_tool_result(text) == text  # below threshold


def test_gate_never_inflates() -> None:
    set_active_compressor(ColumnarCompressor())
    # already-minified short array that columnar can't beat
    text = json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]) + " " * 3000
    out = maybe_compress_tool_result(text)
    assert len(out) <= len(text)


def test_gate_never_raises() -> None:
    class _Boom:
        name = "boom"

        def compress(self, text: str) -> str:
            raise RuntimeError("kaboom")

    set_active_compressor(_Boom())
    text = _big_json_array()
    assert maybe_compress_tool_result(text) == text  # swallowed, original returned


def test_gate_handles_empty_text() -> None:
    set_active_compressor(ColumnarCompressor())
    assert maybe_compress_tool_result("") == ""


def test_set_get_active_compressor() -> None:
    assert get_active_compressor() is None
    c = ColumnarCompressor()
    set_active_compressor(c)
    assert get_active_compressor() is c
