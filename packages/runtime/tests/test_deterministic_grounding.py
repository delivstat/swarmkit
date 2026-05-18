"""Tests for deterministic grounding check (Tier 1).

Verifies that structured output with output_schema gets a deterministic
source check — no LLM call needed. Unsourced claims are flagged.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from swarmkit_runtime.langgraph_compiler._output_gov import (
    _check_deterministic_grounding,
)
from swarmkit_runtime.langgraph_compiler._output_schema import (
    GroundingResult,
    check_grounding,
)

# ---- check_grounding (pure function) ------------------------------------


class TestCheckGrounding:
    def test_all_sourced_passes(self) -> None:
        output = {
            "findings": [
                {"fact": "Config has retries=3", "source": "cdt:search"},
                {"fact": "Timeout is 30s", "source": "cdt:search"},
            ]
        }
        result = check_grounding(output)
        assert result.passed is True
        assert result.total_findings == 2
        assert result.sourced == 2
        assert result.unsourced == []

    def test_unsourced_finding_fails(self) -> None:
        output = {
            "findings": [
                {"fact": "Config has retries=3", "source": "cdt:search"},
                {"fact": "Magic value exists", "source": ""},
            ]
        }
        result = check_grounding(output)
        assert result.passed is False
        assert result.sourced == 1
        assert len(result.unsourced) == 1
        assert "Magic value" in result.unsourced[0]

    def test_missing_source_key_fails(self) -> None:
        output = {
            "findings": [
                {"fact": "No source key at all"},
            ]
        }
        result = check_grounding(output)
        assert result.passed is False
        assert len(result.unsourced) == 1

    def test_unattributed_counted(self) -> None:
        output = {
            "findings": [
                {"fact": "X", "source": "unattributed"},
                {"fact": "Y", "source": "cdt:search"},
            ]
        }
        result = check_grounding(output)
        assert result.passed is True
        assert result.unattributed == 1
        assert result.sourced == 1

    def test_no_findings_passes(self) -> None:
        output: dict[str, Any] = {"findings": []}
        result = check_grounding(output)
        assert result.passed is True
        assert result.total_findings == 0

    def test_no_findings_key_passes(self) -> None:
        output: dict[str, Any] = {"summary": "hello"}
        result = check_grounding(output)
        assert result.passed is True

    def test_non_dict_findings_skipped(self) -> None:
        output: dict[str, Any] = {"findings": ["not a dict", 42]}
        result = check_grounding(output)
        assert result.passed is True
        assert result.total_findings == 0

    def test_long_fact_truncated_in_unsourced(self) -> None:
        long_fact = "A" * 200
        output = {"findings": [{"fact": long_fact, "source": ""}]}
        result = check_grounding(output)
        assert len(result.unsourced[0]) == 120

    def test_frozen_result(self) -> None:
        result = GroundingResult(passed=True)
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]


# ---- _check_deterministic_grounding (integration) -----------------------


class TestCheckDeterministicGrounding:
    @pytest.mark.asyncio
    async def test_passes_records_audit(self) -> None:
        output = json.dumps(
            {
                "findings": [
                    {"fact": "X", "source": "srv:tool"},
                ]
            }
        )
        governance = AsyncMock()
        result = await _check_deterministic_grounding(output, [], "agent-1", governance)
        assert "GROUNDING FLAGS" not in result
        governance.record_event.assert_called_once()
        event = governance.record_event.call_args[0][0]
        assert event.event_type == "grounding.checked"
        assert event.payload["passed"] is True
        assert event.payload["deterministic"] is True

    @pytest.mark.asyncio
    async def test_fails_annotates_output(self) -> None:
        output = json.dumps(
            {
                "findings": [
                    {"fact": "Sourced claim", "source": "srv:tool"},
                    {"fact": "Fabricated claim", "source": ""},
                ]
            }
        )
        governance = AsyncMock()
        result = await _check_deterministic_grounding(output, [], "agent-1", governance)
        assert "GROUNDING FLAGS (deterministic)" in result
        assert "Fabricated claim" in result
        event = governance.record_event.call_args[0][0]
        assert event.payload["passed"] is False
        assert event.payload["unsourced_count"] == 1

    @pytest.mark.asyncio
    async def test_prose_passthrough(self) -> None:
        governance = AsyncMock()
        result = await _check_deterministic_grounding("This is prose", [], "agent-1", governance)
        assert result == "This is prose"
        governance.record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_findings_json_passthrough(self) -> None:
        governance = AsyncMock()
        result = await _check_deterministic_grounding(
            json.dumps({"summary": "hi"}), [], "agent-1", governance
        )
        assert "GROUNDING FLAGS" not in result
        governance.record_event.assert_not_called()
