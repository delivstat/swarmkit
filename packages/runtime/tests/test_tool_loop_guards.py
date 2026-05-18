"""Tests for tool loop deduplication and per-tool call limits.

Prevents degenerate model behaviour where a tool is called 50+ times
with progressively longer queries that never find anything useful.
"""

from __future__ import annotations

from swarmkit_runtime.langgraph_compiler._helpers import ToolCallResult
from swarmkit_runtime.langgraph_compiler._tool_loop import _apply_tool_guards


def _make_result(tool_name: str = "search-docs", idx: int = 0) -> ToolCallResult:
    return ToolCallResult(
        tool_use_id=f"call_{idx}",
        tool_name=tool_name,
        result=f"Result from {tool_name} call {idx}",
        image_blocks=[],
    )


class TestApplyToolGuards:
    def test_under_limit_passes_through(self) -> None:
        counts: dict[str, int] = {}
        results = [_make_result("search-docs", i) for i in range(3)]
        modified, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert not hit
        assert len(modified) == 3
        assert all("TOOL LIMIT" not in r.result for r in modified)
        assert counts["search-docs"] == 3

    def test_over_limit_replaces_result(self) -> None:
        counts: dict[str, int] = {"search-docs": 8}
        results = [_make_result("search-docs", 9)]
        modified, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert hit
        assert "TOOL LIMIT" in modified[0].result
        assert counts["search-docs"] == 9

    def test_different_tools_tracked_separately(self) -> None:
        counts: dict[str, int] = {"search-docs": 8}
        results = [
            _make_result("search-docs", 0),
            _make_result("grep-code", 0),
        ]
        modified, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert hit
        assert "TOOL LIMIT" in modified[0].result
        assert "TOOL LIMIT" not in modified[1].result
        assert counts["grep-code"] == 1

    def test_limit_of_two(self) -> None:
        counts: dict[str, int] = {"tool": 2}
        results = [_make_result("tool", 0)]
        modified, hit = _apply_tool_guards(results, counts, 2, "agent-1")
        assert hit
        assert "TOOL LIMIT" in modified[0].result

    def test_empty_results(self) -> None:
        counts: dict[str, int] = {}
        modified, hit = _apply_tool_guards([], counts, 8, "agent-1")
        assert not hit
        assert modified == []

    def test_accumulates_across_calls(self) -> None:
        counts: dict[str, int] = {}
        for i in range(7):
            results = [_make_result("search", i)]
            _apply_tool_guards(results, counts, 8, "agent-1")
        assert counts["search"] == 7

        results = [_make_result("search", 7)]
        _, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert not hit
        assert counts["search"] == 8

        results = [_make_result("search", 8)]
        _, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert hit
        assert counts["search"] == 9

    def test_stop_message_includes_tool_name(self) -> None:
        counts: dict[str, int] = {"my-tool": 10}
        results = [_make_result("my-tool", 0)]
        modified, _ = _apply_tool_guards(results, counts, 8, "agent-1")
        assert "my-tool" in modified[0].result
        assert "11" in modified[0].result

    def test_read_tool_gets_higher_limit(self) -> None:
        counts: dict[str, int] = {"read-project-code": 9}
        results = [_make_result("read-project-code", 0)]
        modified, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert not hit
        assert "TOOL LIMIT" not in modified[0].result

    def test_read_tool_still_has_limit(self) -> None:
        counts: dict[str, int] = {"read-project-code": 50}
        results = [_make_result("read-project-code", 0)]
        modified, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert hit
        assert "TOOL LIMIT" in modified[0].result

    def test_get_tool_gets_higher_limit(self) -> None:
        counts: dict[str, int] = {"get-service-config": 20}
        results = [_make_result("get-service-config", 0)]
        _, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert not hit

    def test_search_tool_keeps_low_limit(self) -> None:
        counts: dict[str, int] = {"search-project-docs": 8}
        results = [_make_result("search-project-docs", 0)]
        _, hit = _apply_tool_guards(results, counts, 8, "agent-1")
        assert hit
