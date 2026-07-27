"""Demo: the governed-memory persistence skill on a LIVE compiled run
(design/details/governed-memory.md — the persistence-skill wiring).

An agent carrying the `governed-memory` skill emits memory candidates as its output; the compiler's
post_output hook routes each through the governed write path and into the store — the real wiring,
with a scripted model standing in for the LLM (no keys). Run it twice worth of candidates to show
new then reinforce (update-in-place through the hook).
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from sqlalchemy import create_engine
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.governed_memory import GovernedMemoryStore
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)
from swarmkit_runtime.resolver import resolve_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]

_OUTPUT = (
    "I learned some facts.\n"
    '{"memories": ['
    '{"subject": "user:alice", "attribute": "preferred_language", "value": "Python"},'
    '{"subject": "user:alice", "attribute": "editor", "value": "neovim", "type": "profile"}'
    "]}"
)


def _model(text: str) -> MockModelProvider:
    return MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text=text),), stop_reason="end_turn", usage=Usage()
        )
    )


async def main() -> None:
    gm_skill = resolve_workspace(REPO_ROOT / "reference").skills["governed-memory"]
    topo = resolve_workspace(REPO_ROOT / "examples" / "hello-swarm" / "workspace").topologies[
        "hello"
    ]
    topo = dataclasses.replace(
        topo, root=dataclasses.replace(topo.root, skills=(gm_skill,), children=())
    )

    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"))
    graph = compile_topology(
        topo,
        model_provider=_model(_OUTPUT),
        governance=MockGovernanceProvider(allow_all=True),
        governed_memory_store=store,
    )

    print("An agent carrying the governed-memory skill runs; its output writes governed memory:\n")
    for label in ("first run ", "second run"):
        await graph.ainvoke({"input": "remember", "agent_results": {}, "output": ""})
        ops = {m.attribute: (m.value, m.reinforce_count) for m in store.search()}
        print(f"  {label}: {ops}")

    print("\nStored (canonical, one row per fact):")
    for m in store.search():
        print(f"  {m.subject} · {m.attribute} = '{m.value}'  (type={m.type}, x{m.reinforce_count})")
    print("\n✓ the agent proposed; the governed path stored + reinforced in place — no duplicates.")


if __name__ == "__main__":
    asyncio.run(main())
