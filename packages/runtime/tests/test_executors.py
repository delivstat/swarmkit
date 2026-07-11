"""Executor abstraction — registry + resolution (design/details/executor-abstraction.md, P1)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from swarmkit_runtime.archetypes import build_archetype_registry
from swarmkit_runtime.executors import (
    ExecutorError,
    ModelExecutor,
    ResolvedExecutor,
    default_executor_registry,
)
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.skills import build_skill_registry
from swarmkit_runtime.workspace import discover

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_default_registry_has_only_model_in_p1() -> None:
    assert default_executor_registry().kinds() == ["model"]


def test_resolve_none_is_the_model_default() -> None:
    # Absent executor block ⇒ today's behavior, backward-compatible.
    assert default_executor_registry().resolve(None) == ResolvedExecutor(kind="model")


def test_resolve_model_block_carries_ref_and_config() -> None:
    block = SimpleNamespace(kind="model", ref="openrouter/deepseek-v4", config={"temperature": 0.2})
    resolved = default_executor_registry().resolve(block)
    assert resolved == ResolvedExecutor(
        kind="model", ref="openrouter/deepseek-v4", config={"temperature": 0.2}
    )


def test_resolve_unknown_kind_raises() -> None:
    block = SimpleNamespace(kind="harness", ref="claude-code", config={})
    with pytest.raises(ExecutorError, match="unknown executor kind 'harness'"):
        default_executor_registry().resolve(block)


def test_model_executor_config_is_permissive() -> None:
    # Model-call params are open — validation must not reject arbitrary knobs.
    ModelExecutor().validate_config({"temperature": 0.2, "top_p": 0.9, "anything": True})


def test_resolved_agents_carry_the_model_executor_by_default() -> None:
    """End-to-end: every resolved agent carries a ResolvedExecutor (design executor-abstraction);
    with no `executor` block declared, it's the `model` default — no behavior change."""
    repo_root = Path(__file__).resolve().parents[3]
    workspace = resolve_workspace(repo_root / "examples" / "hello-swarm" / "workspace")
    topology = next(iter(workspace.topologies.values()))

    def kinds(agent: object) -> list[str]:
        found = [agent.executor.kind]  # type: ignore[attr-defined]
        for child in agent.children:  # type: ignore[attr-defined]
            found.extend(kinds(child))
        return found

    all_kinds = kinds(topology.root)
    assert all_kinds  # at least the root
    assert set(all_kinds) == {"model"}


def test_resolution_rejects_an_unknown_executor_kind_in_a_workspace() -> None:
    ws = FIXTURES / "workspaces-invalid" / "archetype-bad-executor"
    artifacts = list(discover(ws))
    skills, _ = build_skill_registry(artifacts)
    _registry, errors = build_archetype_registry(artifacts, skills)
    codes = [e.code for e in errors]
    assert "archetype.executor-invalid" in codes
    assert "harness" in next(e.message for e in errors if e.code == "archetype.executor-invalid")
