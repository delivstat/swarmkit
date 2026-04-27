"""Tests for the LangGraph compiler (M3).

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swael_runtime.governance._mock import MockGovernanceProvider
from swael_runtime.langgraph_compiler import compile_topology
from swael_runtime.langgraph_compiler._compiler import _validate_and_correct
from swael_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)
from swael_runtime.model_providers import Message as ProviderMessage
from swael_runtime.resolver import ResolvedAgent, ResolvedTopology, resolve_workspace

# Use a real resolved topology from the hello-swarm example.
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCOPED_WS = FIXTURES / "workspaces" / "resolved-tree"


# ---- helpers for building test topologies --------------------------------


def _simple_topology(*, with_children: bool = False) -> ResolvedTopology:
    """Build a minimal resolved topology for testing."""
    ws = resolve_workspace(EXAMPLE_WS)
    return ws.topologies["hello"]


def _two_agent_topology() -> tuple[ResolvedTopology, ResolvedAgent, ResolvedAgent]:
    topo = _simple_topology()
    root = topo.root
    child = root.children[0]
    return topo, root, child


# ---- compilation ---------------------------------------------------------


def test_compile_produces_graph() -> None:
    topo = _simple_topology()
    mock_model = MockModelProvider()
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)
    assert graph is not None


def test_compiled_graph_has_agent_nodes() -> None:
    topo, root, child = _two_agent_topology()
    mock_model = MockModelProvider()
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)
    node_names = set(graph.get_graph().nodes.keys())
    assert root.id in node_names
    assert child.id in node_names


# ---- execution with mock model ------------------------------------------


@pytest.mark.asyncio
async def test_root_only_execution_returns_output() -> None:
    """When the mock model returns text (no tool calls), the root
    produces output directly without delegation.
    """
    topo = _simple_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="Hello engineers!"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    result = await graph.ainvoke(
        {
            "input": "Greet the team",
            "messages": [],
            "agent_results": {},
            "current_agent": "",
            "output": "",
        }
    )

    assert result["output"] == "Hello engineers!"
    assert "root" in result["agent_results"]


@pytest.mark.asyncio
async def test_delegation_routes_to_child() -> None:
    """Root delegates to child via delegate_to_<child> tool call.
    Child responds with text. Root runs again and produces final output.
    """
    topo, _root, child = _two_agent_topology()

    call_count = 0

    class DelegatingMock(MockModelProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            nonlocal call_count
            call_count += 1

            # First call (root): delegate to child
            if call_count == 1:
                return CompletionResponse(
                    content=(
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="call_1",
                            tool_name=f"delegate_to_{child.id}",
                            tool_input={"task": "Say hello to engineers"},
                        ),
                    ),
                    stop_reason="tool_use",
                    usage=Usage(),
                )
            # Second call (child): respond with greeting
            if call_count == 2:
                return CompletionResponse(
                    content=(ContentBlock(type="text", text="Hey engineers!"),),
                    stop_reason="end_turn",
                    usage=Usage(),
                )
            # Third call (root again): produce final output
            return CompletionResponse(
                content=(ContentBlock(type="text", text="Worker says: Hey engineers!"),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=DelegatingMock(), governance=mock_gov)

    result = await graph.ainvoke(
        {
            "input": "Greet the team",
            "messages": [],
            "agent_results": {},
            "current_agent": "",
            "output": "",
        }
    )

    assert child.id in result["agent_results"]
    assert result["agent_results"][child.id] == "Hey engineers!"
    assert result["output"] is not None
    assert call_count == 3


@pytest.mark.asyncio
async def test_governance_records_completion_events() -> None:
    topo = _simple_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="done"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    await graph.ainvoke(
        {
            "input": "test",
            "messages": [],
            "agent_results": {},
            "current_agent": "",
            "output": "",
        }
    )

    assert len(mock_gov.events) >= 1
    assert any(e.event_type == "agent.completed" for e in mock_gov.events)


@pytest.mark.asyncio
async def test_tools_include_skills_and_delegation() -> None:
    """The mock model records calls — verify the request includes both
    skill tools and delegation tools.
    """
    topo, _root, child = _two_agent_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="done"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    await graph.ainvoke(
        {
            "input": "test",
            "messages": [],
            "agent_results": {},
            "current_agent": "",
            "output": "",
        }
    )

    assert len(mock_model.calls) >= 1
    first_call = mock_model.calls[0]
    tool_names = [t.name for t in (first_call.tools or ())]
    assert f"delegate_to_{child.id}" in tool_names


# ---- governance middleware -----------------------------------------------


def _scoped_topology() -> ResolvedTopology:
    """Topology with agents that have IAM scopes (reviewer has repo:read)."""
    ws = resolve_workspace(SCOPED_WS)
    return ws.topologies["review"]


@pytest.mark.asyncio
async def test_governance_denies_agent_without_required_scopes() -> None:
    """Reviewer has base_scope=[repo:read] but governance provider allows
    nothing → root delegates to reviewer → reviewer is denied.
    """
    topo = _scoped_topology()
    call_count = 0
    reviewer_id = "reviewer"

    class DelegatingMock(MockModelProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CompletionResponse(
                    content=(
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="c1",
                            tool_name=f"delegate_to_{reviewer_id}",
                            tool_input={"task": "review code"},
                        ),
                    ),
                    stop_reason="tool_use",
                    usage=Usage(),
                )
            return CompletionResponse(
                content=(ContentBlock(type="text", text="got denied result"),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    mock_gov = MockGovernanceProvider(allowed_scopes=frozenset())
    graph = compile_topology(topo, model_provider=DelegatingMock(), governance=mock_gov)

    await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    assert any(e.event_type == "policy.denied" for e in mock_gov.events)
    denied = [e for e in mock_gov.events if e.event_type == "policy.denied"]
    assert any(e.agent_id == reviewer_id for e in denied)


@pytest.mark.asyncio
async def test_governance_allows_agent_with_matching_scopes() -> None:
    """Reviewer's repo:read is in the allowed set → model call proceeds."""
    topo = _scoped_topology()

    class DirectMock(MockModelProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            return CompletionResponse(
                content=(ContentBlock(type="text", text="allowed"),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    mock_gov = MockGovernanceProvider(allowed_scopes=frozenset({"repo:read", "repo:write"}))
    graph = compile_topology(topo, model_provider=DirectMock(), governance=mock_gov)

    result = await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    assert result["output"] == "allowed"
    assert not any(e.event_type == "policy.denied" for e in mock_gov.events)


@pytest.mark.asyncio
async def test_governance_deny_records_scopes_in_audit() -> None:
    """Denied agent's audit event includes the missing scopes."""
    topo = _scoped_topology()
    reviewer_id = "reviewer"
    call_count = 0

    class DelegatingMock(MockModelProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CompletionResponse(
                    content=(
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="c1",
                            tool_name=f"delegate_to_{reviewer_id}",
                            tool_input={"task": "review"},
                        ),
                    ),
                    stop_reason="tool_use",
                    usage=Usage(),
                )
            return CompletionResponse(
                content=(ContentBlock(type="text", text="done"),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    mock_gov = MockGovernanceProvider(allowed_scopes=frozenset())
    graph = compile_topology(topo, model_provider=DelegatingMock(), governance=mock_gov)

    await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    denied_events = [e for e in mock_gov.events if e.event_type == "policy.denied"]
    assert len(denied_events) >= 1
    assert "scopes_denied" in denied_events[0].payload


# ---- trust scoring -------------------------------------------------------


@pytest.mark.asyncio
async def test_low_trust_denies_execution() -> None:
    """Agent with trust score below threshold is denied."""
    topo = _simple_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="should not run"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider(trust_scores={"root": 0.1})
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    result = await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    assert "DENIED" in result["output"]
    assert "trust" in result["output"].lower()
    trust_events = [e for e in mock_gov.events if e.event_type == "trust.denied"]
    assert len(trust_events) >= 1
    assert len(mock_model.calls) == 0


@pytest.mark.asyncio
async def test_degraded_trust_logs_warning_but_continues() -> None:
    """Agent with degraded trust executes but logs a warning event."""
    topo = _simple_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="done"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider(trust_scores={"root": 0.5})
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    result = await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    assert result["output"] == "done"
    degraded_events = [e for e in mock_gov.events if e.event_type == "trust.degraded"]
    assert len(degraded_events) >= 1
    assert degraded_events[0].payload["tier"] == "degraded"


@pytest.mark.asyncio
async def test_full_trust_no_warnings() -> None:
    """Agent with full trust — no trust-related events."""
    topo = _simple_topology()
    mock_model = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="done"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider()
    graph = compile_topology(topo, model_provider=mock_model, governance=mock_gov)

    result = await graph.ainvoke(
        {"input": "test", "messages": [], "agent_results": {}, "current_agent": "", "output": ""}
    )

    assert result["output"] == "done"
    trust_events = [
        e for e in mock_gov.events if e.event_type in ("trust.denied", "trust.degraded")
    ]
    assert len(trust_events) == 0


# ---- output governance (auto-correction) ---------------------------------

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "confidence", "reasoning"],
}


@pytest.mark.asyncio
async def test_valid_json_passes_output_validation() -> None:
    """Valid JSON matching schema → output.validated audit event."""
    valid_json = '{"verdict": "pass", "confidence": 0.85, "reasoning": "Code is clean."}'
    mock_model = MockModelProvider()
    mock_gov = MockGovernanceProvider()

    result = await _validate_and_correct(
        valid_json,
        DECISION_SCHEMA,
        model_provider=mock_model,
        model_name="mock",
        system_prompt=None,
        messages=[ProviderMessage(role="user", content="test")],
        governance=mock_gov,
        agent_id="test-agent",
    )

    assert result == valid_json
    validated = [e for e in mock_gov.events if e.event_type == "output.validated"]
    assert len(validated) == 1
    assert validated[0].payload["valid"] is True


@pytest.mark.asyncio
async def test_invalid_json_triggers_autocorrect() -> None:
    """Invalid output → re-prompt with field errors → corrected on retry."""
    call_count = 0

    class CorrectionMock(MockModelProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            nonlocal call_count
            call_count += 1
            good = '{"verdict": "pass", "confidence": 0.85, "reasoning": "Code is clean."}'
            return CompletionResponse(
                content=(ContentBlock(type="text", text=good),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    mock_gov = MockGovernanceProvider()
    bad_json = '{"verdict": "pass", "confidence": 1.5, "reasoning": "ok"}'

    await _validate_and_correct(
        bad_json,
        DECISION_SCHEMA,
        model_provider=CorrectionMock(),
        model_name="mock",
        system_prompt=None,
        messages=[ProviderMessage(role="user", content="test")],
        governance=mock_gov,
        agent_id="test-agent",
    )

    assert call_count >= 1
    validated = [e for e in mock_gov.events if e.event_type == "output.validated"]
    assert len(validated) == 1


@pytest.mark.asyncio
async def test_exhausted_retries_records_failure() -> None:
    """Invalid output every time → validation_failed event after max retries."""
    always_bad = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text='{"verdict": "maybe"}'),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_gov = MockGovernanceProvider()

    await _validate_and_correct(
        '{"verdict": "maybe"}',
        DECISION_SCHEMA,
        model_provider=always_bad,
        model_name="mock",
        system_prompt=None,
        messages=[ProviderMessage(role="user", content="test")],
        governance=mock_gov,
        agent_id="test-agent",
    )

    failed = [e for e in mock_gov.events if e.event_type == "output.validation_failed"]
    assert len(failed) == 1
    assert "errors" in failed[0].payload
