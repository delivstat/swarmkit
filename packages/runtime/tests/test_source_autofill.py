"""Tests for source auto-population from tool call provenance.

Covers: extracting source tags from tool results, validating finding
sources against known tool calls, and auto-filling empty sources.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from swarmkit_runtime.langgraph_compiler._output_gov import (
    _auto_fill_sources,
    _extract_sources_from_messages,
)
from swarmkit_runtime.langgraph_compiler._output_schema import (
    extract_tool_sources,
    validate_and_fill_sources,
)
from swarmkit_runtime.model_providers._types import ContentBlock, Message


@dataclass
class FakeToolCallResult:
    tool_use_id: str = ""
    tool_name: str = ""
    result: str = ""
    image_blocks: list[Any] = field(default_factory=list)


# ---- extract_tool_sources ------------------------------------------------


class TestExtractToolSources:
    def test_extracts_source_tag(self) -> None:
        tr = FakeToolCallResult(
            tool_name="search-config",
            result="Found 3 configs\n[source: cdt-server:search-config | 42ms]",
        )
        sources = extract_tool_sources([tr])
        assert "cdt-server:search-config" in sources

    def test_extracts_tool_name(self) -> None:
        tr = FakeToolCallResult(tool_name="grep-code", result="no provenance tag here")
        sources = extract_tool_sources([tr])
        assert "grep-code" in sources

    def test_multiple_sources(self) -> None:
        results = [
            FakeToolCallResult(
                tool_name="search",
                result="data\n[source: srv1:search | 10ms]",
            ),
            FakeToolCallResult(
                tool_name="read-file",
                result="content\n[source: fs:read-file | 5ms]",
            ),
        ]
        sources = extract_tool_sources(results)
        assert "srv1:search" in sources
        assert "fs:read-file" in sources
        assert "search" in sources
        assert "read-file" in sources

    def test_empty_results(self) -> None:
        assert extract_tool_sources([]) == set()

    def test_no_tag_still_gets_tool_name(self) -> None:
        tr = FakeToolCallResult(tool_name="my-tool", result="plain output")
        sources = extract_tool_sources([tr])
        assert "my-tool" in sources


# ---- validate_and_fill_sources -------------------------------------------


class TestValidateAndFillSources:
    def test_keeps_existing_sources(self) -> None:
        output: dict[str, Any] = {
            "findings": [
                {"fact": "X exists", "source": "cdt:search"},
            ]
        }
        result = validate_and_fill_sources(output, {"cdt:search"})
        assert result["findings"][0]["source"] == "cdt:search"

    def test_auto_fills_single_source(self) -> None:
        output: dict[str, Any] = {
            "findings": [
                {"fact": "X exists", "source": ""},
                {"fact": "Y exists", "source": ""},
            ]
        }
        result = validate_and_fill_sources(output, {"cdt:search"})
        assert result["findings"][0]["source"] == "cdt:search"
        assert result["findings"][1]["source"] == "cdt:search"

    def test_marks_unattributed_with_multiple_sources(self) -> None:
        output: dict[str, Any] = {
            "findings": [
                {"fact": "X exists", "source": ""},
            ]
        }
        result = validate_and_fill_sources(output, {"cdt:search", "fs:read"})
        assert result["findings"][0]["source"] == "unattributed"

    def test_no_known_sources_leaves_empty(self) -> None:
        output: dict[str, Any] = {
            "findings": [
                {"fact": "X exists", "source": ""},
            ]
        }
        result = validate_and_fill_sources(output, set())
        assert result["findings"][0]["source"] == ""

    def test_mixed_filled_and_empty(self) -> None:
        output: dict[str, Any] = {
            "findings": [
                {"fact": "A", "source": "srv:tool1"},
                {"fact": "B", "source": ""},
            ]
        }
        result = validate_and_fill_sources(output, {"srv:tool1"})
        assert result["findings"][0]["source"] == "srv:tool1"
        assert result["findings"][1]["source"] == "srv:tool1"

    def test_no_findings_key(self) -> None:
        output: dict[str, Any] = {"summary": "hello"}
        result = validate_and_fill_sources(output, {"srv:tool"})
        assert result == {"summary": "hello"}

    def test_non_dict_findings_items(self) -> None:
        output: dict[str, Any] = {"findings": ["not a dict"]}
        result = validate_and_fill_sources(output, {"srv:tool"})
        assert result["findings"] == ["not a dict"]


# ---- _extract_sources_from_messages --------------------------------------


class TestExtractSourcesFromMessages:
    def test_extracts_from_tool_result_blocks(self) -> None:
        messages = [
            Message(
                role="user",
                content=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="call_1",
                        tool_result="Config found\n[source: cdt:search | 20ms]",
                    ),
                ],
            ),
        ]
        sources = _extract_sources_from_messages(messages)
        assert "cdt:search" in sources

    def test_extracts_from_text_content(self) -> None:
        messages = [
            Message(
                role="user",
                content="Result: data\n[source: srv:tool | 100ms]",
            ),
        ]
        sources = _extract_sources_from_messages(messages)
        assert "srv:tool" in sources

    def test_no_sources_returns_empty(self) -> None:
        messages = [
            Message(role="user", content="Just a question"),
        ]
        assert _extract_sources_from_messages(messages) == set()

    def test_multiple_messages_multiple_sources(self) -> None:
        messages = [
            Message(
                role="user",
                content=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="c1",
                        tool_result="data\n[source: s1:t1 | 10ms]",
                    ),
                ],
            ),
            Message(
                role="user",
                content=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="c2",
                        tool_result="more\n[source: s2:t2 | 20ms]",
                    ),
                ],
            ),
        ]
        sources = _extract_sources_from_messages(messages)
        assert sources == {"s1:t1", "s2:t2"}


# ---- _auto_fill_sources (integration) -----------------------------------


class TestAutoFillSourcesIntegration:
    def test_fills_from_messages(self) -> None:
        output = json.dumps(
            {
                "findings": [
                    {"fact": "X", "source": ""},
                ],
            }
        )
        messages = [
            Message(
                role="user",
                content=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="c1",
                        tool_result="data\n[source: cdt:search | 50ms]",
                    ),
                ],
            ),
        ]
        result = _auto_fill_sources(output, messages)
        parsed = json.loads(result)
        assert parsed["findings"][0]["source"] == "cdt:search"

    def test_passthrough_for_prose(self) -> None:
        output = "This is prose, not JSON."
        messages: list[Message] = []
        assert _auto_fill_sources(output, messages) == output

    def test_passthrough_for_non_findings_json(self) -> None:
        output = json.dumps({"summary": "hello"})
        messages: list[Message] = []
        assert _auto_fill_sources(output, messages) == output
