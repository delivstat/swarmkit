"""Persistence-skill wiring: an agent carrying the `governed-memory` skill writes to the store on a
live run, via the compiler post_output hook (design/details/governed-memory.md).

Covers the hook (parse + governed write) and the end-to-end compiler path (a scripted agent output
lands governed in the store), plus the WorkspaceRuntime service construction.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    governed_memory_post_output,
    parse_candidates,
)
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    ProviderRegistry,
    Usage,
)
from swarmkit_runtime.resolver import resolve_workspace

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_WS = REPO_ROOT / "reference"
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

_CANDIDATES_JSON = (
    '{"memories": ['
    '{"subject": "user:alice", "attribute": "preferred_language", "value": "Python"},'
    '{"subject": "user:alice", "attribute": "timezone", "value": "IST", "type": "profile"}'
    "]}"
)


def _store() -> GovernedMemoryStore:
    return GovernedMemoryStore(create_engine("sqlite:///:memory:"))


def _model(text: str) -> MockModelProvider:
    return MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text=text),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )


# ── the parse contract ───────────────────────────────────────────────────────────────────────────
async def test_parse_candidates_extracts_valid_and_skips_bad() -> None:
    text = f"Here is what I learned:\n{_CANDIDATES_JSON}\nThanks!"  # prose-wrapped
    cands = parse_candidates(text)
    assert [(c.subject, c.attribute, c.value, c.type) for c in cands] == [
        ("user:alice", "preferred_language", "Python", "semantic"),
        ("user:alice", "timezone", "IST", "profile"),
    ]


async def test_parse_candidates_tolerant_of_junk() -> None:
    assert parse_candidates("no json here") == []
    assert parse_candidates('{"nope": 1}') == []
    # entries missing required fields are skipped, not fatal
    assert parse_candidates('{"memories": [{"subject": "s", "value": "v"}]}') == []


# ── the hook ─────────────────────────────────────────────────────────────────────────────────────
async def test_hook_writes_candidates_governed() -> None:
    store = _store()
    summary = await governed_memory_post_output(
        agent_id="curator", agent_output=_CANDIDATES_JSON, store=store
    )
    assert summary["written"] == 2 and summary["by_op"] == {"new": 2}
    assert store.get("user:alice", "preferred_language").value == "Python"  # type: ignore[union-attr]
    # a second identical pass reinforces (update-in-place discipline holds through the hook)
    again = await governed_memory_post_output(
        agent_id="curator", agent_output=_CANDIDATES_JSON, store=store
    )
    assert again["by_op"] == {"reinforce": 2}


# ── the compiler end-to-end ──────────────────────────────────────────────────────────────────────
async def test_agent_with_skill_writes_memory_on_run() -> None:
    gm_skill = resolve_workspace(REFERENCE_WS).skills["governed-memory"]
    topo = resolve_workspace(EXAMPLE_WS).topologies["hello"]
    # attach the persistence skill to the (childless) root so its output routes through the store
    root = dataclasses.replace(topo.root, skills=(gm_skill,), children=())
    topo = dataclasses.replace(topo, root=root)

    store = _store()
    graph = compile_topology(
        topo,
        model_provider=_model(_CANDIDATES_JSON),
        governance=MockGovernanceProvider(allow_all=True),
        governed_memory_store=store,
    )
    await graph.ainvoke({"input": "remember these", "agent_results": {}, "output": ""})

    assert store.get("user:alice", "preferred_language").value == "Python"  # type: ignore[union-attr]
    assert store.get("user:alice", "timezone").type == "profile"  # type: ignore[union-attr]


async def test_agent_without_skill_writes_nothing() -> None:
    topo = resolve_workspace(EXAMPLE_WS).topologies["hello"]
    topo = dataclasses.replace(topo, root=dataclasses.replace(topo.root, children=()))
    store = _store()
    graph = compile_topology(
        topo,
        model_provider=_model(_CANDIDATES_JSON),
        governance=MockGovernanceProvider(allow_all=True),
        governed_memory_store=store,  # store wired, but the agent lacks the skill → no write
    )
    await graph.ainvoke({"input": "hi", "agent_results": {}, "output": ""})
    assert store.search() == []


# ── the service layer ────────────────────────────────────────────────────────────────────────────
async def test_workspace_runtime_builds_wired_store() -> None:
    workspace = resolve_workspace(REFERENCE_WS)  # ships governed-memory + memory-reconcile
    registry = ProviderRegistry()
    registry.register(MockModelProvider())
    runtime = WorkspaceRuntime(
        workspace=workspace,
        workspace_root=REFERENCE_WS,
        provider_registry=registry,
        governance=MockGovernanceProvider(allow_all=True),
        mcp_manager=None,
    )
    store = runtime.governed_memory
    assert isinstance(store, GovernedMemoryStore)
    assert store._reconciler is not None  # memory-reconcile is present → reconciler wired
