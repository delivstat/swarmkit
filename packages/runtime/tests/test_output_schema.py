"""Tests for structured output_schema on workers.

Covers: default schema application, opt-out, system prompt injection,
response_format on CompletionRequest, summarizer bypass, and the
_looks_incomplete guard for valid JSON.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from swarmkit_runtime.langgraph_compiler._output_gov import _get_outputs_schema
from swarmkit_runtime.langgraph_compiler._output_schema import (
    WORKER_DEFAULT_OUTPUT_SCHEMA,
    get_effective_output_schema,
)
from swarmkit_runtime.langgraph_compiler._prompts import (
    _build_completion_request,
    _build_system_prompt,
    _looks_incomplete,
)
from swarmkit_runtime.langgraph_compiler._task_executor import _summarize_result
from swarmkit_runtime.resolver._resolved import ResolvedAgent


def _make_agent(
    role: str = "worker",
    output_schema: dict[str, Any] | None = None,
    output_schema_disabled: bool = False,
) -> ResolvedAgent:
    return ResolvedAgent(
        id=f"test-{role}",
        role=role,  # type: ignore[arg-type]
        model={"name": "mock"},
        prompt={"system": "You are a test agent."},
        skills=(),
        iam=None,
        output_schema=output_schema,
        output_schema_disabled=output_schema_disabled,
    )


# ---- get_effective_output_schema -----------------------------------------


class TestGetEffectiveOutputSchema:
    def test_worker_gets_default_schema(self) -> None:
        agent = _make_agent(role="worker")
        schema = get_effective_output_schema(agent)
        assert schema is not None
        assert schema == WORKER_DEFAULT_OUTPUT_SCHEMA
        assert "findings" in schema["properties"]

    def test_leader_no_default(self) -> None:
        agent = _make_agent(role="leader")
        assert get_effective_output_schema(agent) is None

    def test_root_no_default(self) -> None:
        agent = _make_agent(role="root")
        assert get_effective_output_schema(agent) is None

    def test_explicit_schema_overrides_default(self) -> None:
        custom = {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        }
        agent = _make_agent(role="worker", output_schema=custom)
        schema = get_effective_output_schema(agent)
        assert schema is not None
        assert "summary" in schema["properties"]
        assert "findings" not in schema.get("properties", {})

    def test_opt_out_returns_none(self) -> None:
        agent = _make_agent(role="worker", output_schema_disabled=True)
        assert get_effective_output_schema(agent) is None

    def test_leader_with_explicit_schema(self) -> None:
        custom = {"type": "object", "properties": {"result": {"type": "string"}}}
        agent = _make_agent(role="leader", output_schema=custom)
        schema = get_effective_output_schema(agent)
        assert schema is not None
        assert "result" in schema["properties"]


# ---- system prompt -------------------------------------------------------


class TestSystemPrompt:
    def test_worker_prompt_includes_schema(self) -> None:
        agent = _make_agent(role="worker")
        prompt = _build_system_prompt(agent, [])
        assert prompt is not None
        assert "STRUCTURED OUTPUT" in prompt
        assert '"findings"' in prompt

    def test_leader_prompt_no_schema(self) -> None:
        agent = _make_agent(role="leader")
        prompt = _build_system_prompt(agent, [])
        if prompt:
            assert "STRUCTURED OUTPUT" not in prompt

    def test_opted_out_worker_no_schema(self) -> None:
        agent = _make_agent(role="worker", output_schema_disabled=True)
        prompt = _build_system_prompt(agent, [])
        if prompt:
            assert "STRUCTURED OUTPUT" not in prompt


# ---- completion request --------------------------------------------------


class TestCompletionRequest:
    def test_worker_has_response_format(self) -> None:
        agent = _make_agent(role="worker")
        req = _build_completion_request("mock", [], None, [], agent)
        assert req.response_format is not None
        # The actual schema is carried (not just "json_object") so providers can
        # do true schema-constrained decoding.
        assert req.response_format["type"] == "json_schema"
        carried = req.response_format["json_schema"]["schema"]
        assert "findings" in carried["properties"]

    def test_explicit_schema_carried_in_response_format(self) -> None:
        custom = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        agent = _make_agent(role="worker", output_schema=custom)
        req = _build_completion_request("mock", [], None, [], agent)
        assert req.response_format is not None
        assert req.response_format["type"] == "json_schema"
        assert req.response_format["json_schema"]["schema"] == custom

    def test_leader_no_response_format(self) -> None:
        agent = _make_agent(role="leader")
        req = _build_completion_request("mock", [], None, [], agent)
        assert req.response_format is None

    def test_opted_out_worker_no_response_format(self) -> None:
        agent = _make_agent(role="worker", output_schema_disabled=True)
        req = _build_completion_request("mock", [], None, [], agent)
        assert req.response_format is None


# ---- _looks_incomplete ---------------------------------------------------


class TestLooksIncomplete:
    def test_valid_json_not_incomplete(self) -> None:
        assert _looks_incomplete('{"findings": []}') is False

    def test_structured_findings_not_incomplete(self) -> None:
        result = json.dumps(
            {
                "findings": [{"fact": "X exists", "source": "grep"}],
                "not_found": [],
            }
        )
        assert _looks_incomplete(result) is False

    def test_planning_text_is_incomplete(self) -> None:
        assert _looks_incomplete("Let me search for the config file") is True

    def test_empty_is_incomplete(self) -> None:
        assert _looks_incomplete("") is True
        assert _looks_incomplete("(no response)") is True


# ---- summarizer bypass ---------------------------------------------------


class TestSummarizerBypass:
    @pytest.mark.asyncio
    async def test_structured_json_skips_llm(self) -> None:
        result = json.dumps(
            {
                "findings": [
                    {"fact": "Config has max_retries=3", "source": "cdt:search-config"},
                    {"fact": "Pipeline timeout is 30s", "source": "cdt:search-config"},
                ],
                "not_found": ["error handling policy"],
            }
        )
        mock_provider = AsyncMock()
        findings = await _summarize_result("task-1", result, mock_provider)
        mock_provider.complete.assert_not_called()
        assert len(findings) == 2
        assert "Config has max_retries=3" in findings[0]
        assert "[cdt:search-config]" in findings[0]

    @pytest.mark.asyncio
    async def test_prose_falls_through_to_llm(self) -> None:
        result = "The configuration analysis reveals several important settings..." * 20
        mock_provider = AsyncMock()
        mock_provider.complete.return_value = AsyncMock(
            content=[AsyncMock(type="text", text="- Finding 1\n- Finding 2")],
        )
        await _summarize_result("task-1", result, mock_provider, coordinator_model="mock")
        mock_provider.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_findings_returns_empty(self) -> None:
        result = json.dumps({"findings": [], "not_found": ["everything"]})
        mock_provider = AsyncMock()
        findings = await _summarize_result("task-1", result, mock_provider)
        mock_provider.complete.assert_not_called()
        assert findings == []

    @pytest.mark.asyncio
    async def test_findings_without_source(self) -> None:
        result = json.dumps(
            {
                "findings": [{"fact": "Something found", "source": ""}],
            }
        )
        mock_provider = AsyncMock()
        findings = await _summarize_result("task-1", result, mock_provider)
        mock_provider.complete.assert_not_called()
        assert findings == ["Something found"]


# ---- output governance integration ---------------------------------------


class TestOutputGovernance:
    def test_worker_gets_schema_from_output_gov(self) -> None:
        agent = _make_agent(role="worker")
        schema = _get_outputs_schema(agent)
        assert schema is not None
        assert "findings" in schema["properties"]

    def test_leader_no_schema_from_output_gov(self) -> None:
        agent = _make_agent(role="leader")
        schema = _get_outputs_schema(agent)
        assert schema is None
