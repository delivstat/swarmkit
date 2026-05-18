"""Tests for synthesis fallback — extracting findings from tool results.

When a worker hits the turn limit and synthesis fails, the runtime
extracts findings from the provenance-tagged tool results rather
than returning empty.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
from swarmkit_runtime.langgraph_compiler._task_executor import (
    _extract_findings_from_messages,
)
from swarmkit_runtime.model_providers._types import ContentBlock, Message


class TestExtractFindingsFromMessages:
    def test_extracts_from_provenance_tags(self) -> None:
        messages = [
            HumanMessage(content="search for returns"),
            AIMessage(
                content="Found pipeline CROMA_RETURN_PIPELINE with 5 conditions\n"
                "[source: sterling-config:get-pipeline | 42ms]"
            ),
        ]
        result = _extract_findings_from_messages(messages)
        assert result != ""
        parsed = json.loads(result)
        assert len(parsed["findings"]) >= 1
        assert "sterling-config:get-pipeline" in parsed["findings"][0]["source"]

    def test_extracts_from_tool_result_blocks(self) -> None:
        messages = [
            Message(
                role="user",
                content=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="call_1",
                        tool_result=(
                            "Pipeline CROMA_PH2_RETURN_PIPELINE has status lifecycle "
                            "1000→1100→1300→3200→3350→3700\n"
                            "[source: sterling-config:get-pipeline | 30ms]"
                        ),
                    ),
                ],
            ),
        ]
        result = _extract_findings_from_messages(messages)
        assert result != ""
        parsed = json.loads(result)
        assert len(parsed["findings"]) >= 1

    def test_empty_messages_returns_empty(self) -> None:
        assert _extract_findings_from_messages([]) == ""

    def test_no_provenance_returns_empty(self) -> None:
        messages = [
            HumanMessage(content="just a question"),
            AIMessage(content="just an answer without provenance"),
        ]
        assert _extract_findings_from_messages(messages) == ""

    def test_caps_at_20_findings(self) -> None:
        messages = []
        for i in range(30):
            messages.append(
                AIMessage(
                    content=f"Finding number {i} with sufficient length for extraction\n"
                    f"[source: tool-{i}:search | {i}ms]"
                )
            )
        result = _extract_findings_from_messages(messages)
        parsed = json.loads(result)
        assert len(parsed["findings"]) <= 20

    def test_includes_not_found_marker(self) -> None:
        messages = [
            AIMessage(
                content="Some data was found in the config search results here\n"
                "[source: cdt:search | 10ms]"
            ),
        ]
        result = _extract_findings_from_messages(messages)
        parsed = json.loads(result)
        assert "not_found" in parsed
        assert "synthesis failed" in parsed["not_found"][0]
