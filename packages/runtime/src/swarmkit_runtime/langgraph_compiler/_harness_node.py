"""Harness-executor dispatch for agent nodes (executor-abstraction §5, P2).

`_build_agent_node` runs the model tool-loop for `executor.kind == "model"` (byte-identical to
before the executor seam) and, for any other kind, hands off here. In P2 PR2 this is a guarded stub:
the dispatch seam lands and every model run is unchanged, but a harness node fails loudly rather
than silently degrading. The real runner — preflight, worktree sandbox, `Executor.run()` streaming
`ExecEvent`s, budget/idle enforcement, artifact collection — lands in P2 PR6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from swarmkit_runtime.executors import ExecutorError

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.langgraph_compiler._state import SwarmState
    from swarmkit_runtime.resolver import ResolvedAgent


async def run_harness_node(
    agent: ResolvedAgent,
    state: SwarmState,
    governance: GovernanceProvider,
) -> dict[str, Any]:
    """Execute an agent whose ``executor.kind`` is not ``model``.

    P2 PR2 stub: raises :class:`ExecutorError`. The seam is live so harness topologies resolve,
    compile, and route here — they just cannot execute until the harness runner lands (P2 PR6).
    """
    raise ExecutorError(
        f"harness execution not yet available: agent {agent.id!r} declares executor "
        f"{agent.executor.kind!r}, but only 'model' can execute in this build "
        "(the harness runner lands in executor P2 PR6)"
    )
