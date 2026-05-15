"""DAG-based execution of child agents with dependency ordering.

When children declare ``depends_on``, the compiler runs them in
topological waves rather than flat parallel or serial delegation.
"""

from __future__ import annotations

import sys
from typing import Any

from langchain_core.messages import HumanMessage

from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry
from swarmkit_runtime.resolver import ResolvedAgent

from ._state import SwarmState


def _has_dag_deps(agent: ResolvedAgent) -> bool:
    """Check if any child agent has depends_on declarations."""
    return any(c.depends_on for c in agent.children)


async def _run_dag(
    agent: ResolvedAgent,
    agent_id: str,
    task: str,
    model_provider: ModelProviderProtocol,
    governance: GovernanceProvider,
    all_agents: dict[str, ResolvedAgent],
    mcp_manager: Any,
    provider_registry: ProviderRegistry | None,
    verbose: str,
) -> dict[str, str]:
    """Execute child agents in dependency order. Returns {child_id: result}."""
    import asyncio as _asyncio  # noqa: PLC0415

    children = {c.id: c for c in agent.children}
    results: dict[str, str] = {}
    completed: set[str] = set()

    def _ready(child: ResolvedAgent) -> bool:
        if child.id in completed:
            return False
        return all(d in completed for d in child.depends_on)

    max_rounds = len(children) + 1
    for _round in range(max_rounds):
        runnable = [c for c in children.values() if _ready(c)]
        if not runnable:
            break

        if verbose:
            names = [c.id for c in runnable]
            print(f"  [dag round {_round + 1}: running {names}]", file=sys.stderr)

        async def _exec_child(
            child: ResolvedAgent,
            _children: dict[str, ResolvedAgent] = children,
        ) -> tuple[str, str]:
            from ._compiler import _build_agent_node, _resolve_agent_provider  # noqa: PLC0415

            dep_context = ""
            if child.depends_on:
                parts = []
                for dep_id in child.depends_on:
                    if dep_id in results:
                        parts.append(f"[{dep_id}]:\n{results[dep_id]}")
                if parts:
                    dep_context = (
                        "Previous agents produced these results:\n\n" + "\n\n".join(parts) + "\n\n"
                    )

            child_input = f"{dep_context}Your task: {task}"
            child_state: SwarmState = {
                "input": child_input,
                "messages": [HumanMessage(content=child_input, name=agent_id)],
                "agent_results": {},
                "delegation_counts": {},
                "task_plan": {},
                "current_agent": child.id,
                "output": "",
            }
            child_provider = _resolve_agent_provider(
                child,
                provider_registry,
                model_provider,
            )
            child_fn = _build_agent_node(
                child,
                child_provider,
                governance,
                all_agents or {},
                mcp_manager,
                provider_registry,
            )
            result_state = await child_fn(child_state)
            return (child.id, result_state.get("output", "(no response)"))

        batch = await _asyncio.gather(*[_exec_child(c) for c in runnable])
        for cid, result in batch:
            results[cid] = result
            completed.add(cid)

    return results
