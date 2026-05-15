"""Tests for DAG dependency graph in agent topologies."""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._dag import _has_dag_deps, _run_dag
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.resolver._resolved import AgentRole, ResolvedAgent
from swarmkit_runtime.resolver._topology import _validate_dag


def _make_agent(
    agent_id: str,
    role: AgentRole = "worker",
    children: tuple[ResolvedAgent, ...] = (),
    depends_on: tuple[str, ...] = (),
) -> ResolvedAgent:
    return ResolvedAgent(
        id=agent_id,
        role=role,
        model=None,
        prompt=None,
        skills=(),
        iam=None,
        children=children,
        depends_on=depends_on,
    )


FAKE_PATH = Path("test.yaml")


class TestDAGValidation:
    """Test _validate_dag for cycle detection, unknown refs, self-refs."""

    def test_no_deps_no_errors(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a"),
                _make_agent("b"),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        assert errors == []

    def test_valid_linear_deps(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a"),
                _make_agent("b", depends_on=("a",)),
                _make_agent("c", depends_on=("b",)),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        assert errors == []

    def test_valid_parallel_then_merge(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a"),
                _make_agent("b"),
                _make_agent("c", depends_on=("a", "b")),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        assert errors == []

    def test_self_reference(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(_make_agent("a", depends_on=("a",)),),
        )
        errors = _validate_dag(root, FAKE_PATH)
        codes = {e.code for e in errors}
        assert "agent.depends-on-self" in codes

    def test_unknown_reference(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(_make_agent("a", depends_on=("nonexistent",)),),
        )
        errors = _validate_dag(root, FAKE_PATH)
        assert len(errors) == 1
        assert errors[0].code == "agent.depends-on-unknown"

    def test_cycle_detection(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a", depends_on=("b",)),
                _make_agent("b", depends_on=("a",)),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        cycle_errors = [e for e in errors if e.code == "agent.depends-on-cycle"]
        assert len(cycle_errors) >= 1

    def test_three_node_cycle(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a", depends_on=("c",)),
                _make_agent("b", depends_on=("a",)),
                _make_agent("c", depends_on=("b",)),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        cycle_errors = [e for e in errors if e.code == "agent.depends-on-cycle"]
        assert len(cycle_errors) >= 1

    def test_multiple_errors(self) -> None:
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a", depends_on=("a",)),  # self-ref
                _make_agent("b", depends_on=("ghost",)),  # unknown
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        codes = {e.code for e in errors}
        assert "agent.depends-on-self" in codes
        assert "agent.depends-on-unknown" in codes

    def test_nested_children_with_deps(self) -> None:
        """Deps work within nested leader-worker hierarchies."""
        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent(
                    "leader",
                    role="leader",
                    children=(
                        _make_agent("w1"),
                        _make_agent("w2", depends_on=("w1",)),
                    ),
                ),
            ),
        )
        errors = _validate_dag(root, FAKE_PATH)
        assert errors == []


class TestHasDAGDeps:
    """Test _has_dag_deps helper."""

    def test_no_deps(self) -> None:

        agent = _make_agent(
            "root",
            children=(
                _make_agent("a"),
                _make_agent("b"),
            ),
        )
        assert not _has_dag_deps(agent)

    def test_with_deps(self) -> None:

        agent = _make_agent(
            "root",
            children=(
                _make_agent("a"),
                _make_agent("b", depends_on=("a",)),
            ),
        )
        assert _has_dag_deps(agent)


class TestDAGExecution:
    """Test _run_dag executes agents in dependency order."""

    @pytest.mark.asyncio
    async def test_linear_dag(self) -> None:

        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("step1"),
                _make_agent("step2", depends_on=("step1",)),
            ),
        )
        all_agents = {"root": root, "step1": root.children[0], "step2": root.children[1]}

        results = await _run_dag(
            agent=root,
            agent_id="root",
            task="test task",
            model_provider=MockModelProvider(),
            governance=MockGovernanceProvider(),
            all_agents=all_agents,
            mcp_manager=None,
            provider_registry=None,
            verbose="",
        )

        assert "step1" in results
        assert "step2" in results

    @pytest.mark.asyncio
    async def test_parallel_then_merge(self) -> None:

        root = _make_agent(
            "root",
            role="root",
            children=(
                _make_agent("a"),
                _make_agent("b"),
                _make_agent("merge", depends_on=("a", "b")),
            ),
        )
        all_agents = {
            "root": root,
            "a": root.children[0],
            "b": root.children[1],
            "merge": root.children[2],
        }

        results = await _run_dag(
            agent=root,
            agent_id="root",
            task="test task",
            model_provider=MockModelProvider(),
            governance=MockGovernanceProvider(),
            all_agents=all_agents,
            mcp_manager=None,
            provider_registry=None,
            verbose="",
        )

        assert "a" in results
        assert "b" in results
        assert "merge" in results
