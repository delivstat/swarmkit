"""Tests for the LangGraph compiler (M3).

See ``design/details/langgraph-compiler.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology, resolve_workspace

# Use a real resolved topology from the hello-swarm example.
REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


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
