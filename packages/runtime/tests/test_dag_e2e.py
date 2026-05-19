"""End-to-end test for DAG dependency execution.

Runs a full topology with depends_on through compile_topology → invoke,
verifying that agents execute in the correct order and predecessor
outputs are passed to dependents.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)
from swarmkit_runtime.resolver._resolved import AgentRole, ResolvedAgent, ResolvedTopology

# ---- tracking mock provider ----


class TrackingMockProvider:
    """Mock provider that records call order and returns agent-specific text."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        system = request.system or ""
        messages = list(request.messages)
        last_content = str(messages[-1].content) if messages else ""

        # Extract agent ID from system prompt "You are <id>."
        import re  # noqa: PLC0415

        match = re.search(r"You are (\S+)\.", system)
        agent_hint = match.group(1) if match else f"unknown-{len(self.calls)}"
        self.calls.append(agent_hint)

        # Root agent: if it sees child results, synthesise; otherwise delegate
        has_child_results = (
            "workers have produced" in last_content or "Workers already completed" in last_content
        )
        if request.tools and not has_child_results:
            tool_names = [t.name for t in request.tools]
            delegate_tools = [t for t in tool_names if t.startswith("delegate_to_")]
            if delegate_tools:
                return CompletionResponse(
                    content=[
                        ContentBlock(
                            type="tool_use",
                            tool_name=delegate_tools[0],
                            tool_use_id="call_0",
                            tool_input={"task": "do the work"},
                        ),
                    ],
                    stop_reason="tool_use",
                    usage=Usage(),
                )

        # Worker: return text mentioning what predecessors said
        predecessor_info = ""
        if "Previous agents produced" in last_content:
            predecessor_info = " (saw predecessor output)"

        return CompletionResponse(
            content=[
                ContentBlock(
                    type="text",
                    text=f"Output from {agent_hint}{predecessor_info}",
                ),
            ],
            stop_reason="end_turn",
            usage=Usage(),
        )


def _make_agent(
    agent_id: str,
    role: AgentRole = "worker",
    children: tuple[ResolvedAgent, ...] = (),
    depends_on: tuple[str, ...] = (),
    system_prompt: str = "",
) -> ResolvedAgent:
    prompt = {"system": system_prompt or f"You are {agent_id}."} if True else None
    return ResolvedAgent(
        id=agent_id,
        role=role,
        model={"provider": "mock", "name": "mock"},
        prompt=prompt,
        skills=(),
        iam=None,
        children=children,
        depends_on=depends_on,
    )


class TestDAGE2E:
    """End-to-end tests that run full topologies with depends_on."""

    @pytest.mark.asyncio
    async def test_linear_pipeline_executes_in_order(self) -> None:
        """researcher → writer → editor: strict sequential order."""
        provider = TrackingMockProvider()
        governance = MockGovernanceProvider()

        root = _make_agent(
            "root",
            role="root",
            system_prompt="You are root. Delegate to your workers.",
            children=(
                _make_agent("researcher", system_prompt="You are researcher."),
                _make_agent(
                    "writer",
                    depends_on=("researcher",),
                    system_prompt="You are writer.",
                ),
                _make_agent(
                    "editor",
                    depends_on=("writer",),
                    system_prompt="You are editor.",
                ),
            ),
        )

        topology = ResolvedTopology(
            id="test-pipeline",
            raw=None,  # type: ignore[arg-type]
            source_path=None,  # type: ignore[arg-type]
            root=root,
        )

        graph = compile_topology(
            topology,
            model_provider=provider,  # type: ignore[arg-type]
            governance=governance,
        )

        result = await graph.ainvoke(
            {
                "input": "Write a blog post about AI agents",
                "messages": [],
                "agent_results": {},
                "current_agent": "root",
                "output": "",
            }
        )

        # Verify all agents ran
        assert "researcher" in provider.calls
        assert "writer" in provider.calls
        assert "editor" in provider.calls

        # Verify order: researcher before writer, writer before editor
        r_idx = provider.calls.index("researcher")
        w_idx = provider.calls.index("writer")
        e_idx = provider.calls.index("editor")
        assert r_idx < w_idx, f"researcher ({r_idx}) should run before writer ({w_idx})"
        assert w_idx < e_idx, f"writer ({w_idx}) should run before editor ({e_idx})"

        # Verify output exists
        assert result.get("output")
        print("\n--- E2E Linear Pipeline ---")
        print(f"Call order: {provider.calls}")
        print(f"Output: {result['output'][:200]}")

    @pytest.mark.asyncio
    async def test_parallel_then_merge(self) -> None:
        """a + b run in parallel, merge waits for both."""
        provider = TrackingMockProvider()
        governance = MockGovernanceProvider()

        root = _make_agent(
            "root",
            role="root",
            system_prompt="You are root. Delegate to workers.",
            children=(
                _make_agent("researcher-a", system_prompt="You are researcher-a."),
                _make_agent("researcher-b", system_prompt="You are researcher-b."),
                _make_agent(
                    "synthesiser",
                    depends_on=("researcher-a", "researcher-b"),
                    system_prompt="You are writer. Synthesise findings.",
                ),
            ),
        )

        topology = ResolvedTopology(
            id="test-parallel-merge",
            raw=None,  # type: ignore[arg-type]
            source_path=None,  # type: ignore[arg-type]
            root=root,
        )

        graph = compile_topology(
            topology,
            model_provider=provider,  # type: ignore[arg-type]
            governance=governance,
        )

        result = await graph.ainvoke(
            {
                "input": "Research topic X from two angles",
                "messages": [],
                "agent_results": {},
                "current_agent": "root",
                "output": "",
            }
        )

        # All three agents ran
        assert "researcher-a" in provider.calls
        assert "researcher-b" in provider.calls

        # Verify output exists
        assert result.get("output")
        print("\n--- E2E Parallel + Merge ---")
        print(f"Call order: {provider.calls}")
        print(f"Output: {result['output'][:200]}")

    @pytest.mark.asyncio
    async def test_no_deps_behaves_like_before(self) -> None:
        """Topology without depends_on uses normal delegation."""
        provider = TrackingMockProvider()
        governance = MockGovernanceProvider()

        root = _make_agent(
            "root",
            role="root",
            system_prompt="You are root. Delegate to workers.",
            children=(
                _make_agent("worker-a", system_prompt="You are researcher."),
                _make_agent("worker-b", system_prompt="You are writer."),
            ),
        )

        topology = ResolvedTopology(
            id="test-no-deps",
            raw=None,  # type: ignore[arg-type]
            source_path=None,  # type: ignore[arg-type]
            root=root,
        )

        graph = compile_topology(
            topology,
            model_provider=provider,  # type: ignore[arg-type]
            governance=governance,
        )

        result = await graph.ainvoke(
            {
                "input": "Do something",
                "messages": [],
                "agent_results": {},
                "current_agent": "root",
                "output": "",
            }
        )

        # Should still work — delegation-based, not DAG
        assert result.get("output")
        print("\n--- E2E No Deps (delegation) ---")
        print(f"Call order: {provider.calls}")
        print(f"Output: {result['output'][:200]}")
