"""What an ``agent`` skill call needs from the runtime, as a per-run context variable.

The compiler passes ``command_packs`` and ``mcp_manager`` positionally through six layers to reach
the skill executor. This is the same kind of dependency — how to start a child run, where the
credentials are, where questions for a human go — but it belongs to the *run*, not the compile,
and a context variable is how the runtime already scopes the run id, the labels, the trace and the
stop checker. Installed by ``WorkspaceRuntime.run`` for the run's duration; absent outside a run,
in which case an ``agent`` skill reports that it cannot be called here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

#: Start a topology of this workspace as a child of the current run:
#: ``(topology_name, user_input) -> ChildRunOutcome``.
ChildRunner = Callable[[str, str], Awaitable["ChildRunOutcome"]]


@dataclass(frozen=True)
class ChildRunOutcome:
    """What a local child run came back with — its output, or the gate it parked on."""

    run_id: str
    output: str = ""
    #: Set when the child parked on a human gate (``GateDeferredError``); the caller cannot
    #: resolve it, only report it.
    gate_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgentSkillContext:
    """The runtime pieces one call reaches for. Every field optional: a missing one degrades that
    capability with a named reason rather than failing the call somewhere else."""

    run_child: ChildRunner | None = None
    credential_service: Any = None
    review_queue: Any = None
    governance: Any = None
    topology_id: str = ""
    #: How many agent skills deep this run already is. A topology that calls a topology that calls
    #: it back would otherwise recurse until the recursion limit of the innermost graph — which is
    #: a lot of model calls to discover a cycle.
    depth: int = 0
    #: An httpx transport for the remote form — a test hands in an ASGI app; production leaves it
    #: None and the network is used.
    transport: Any = None
    #: How long a relayed question waits for a person before the call fails (never hangs).
    relay_wait_s: float | None = None


#: A topology calling a topology calling a topology is plenty; deeper is a cycle until proven
#: otherwise.
MAX_DEPTH = 3

_agent_context: ContextVar[AgentSkillContext | None] = ContextVar(
    "swarmkit_agent_skill_context", default=None
)


def current_agent_context() -> AgentSkillContext | None:
    return _agent_context.get()


def set_agent_context(ctx: AgentSkillContext | None) -> Token[AgentSkillContext | None]:
    return _agent_context.set(ctx)


def reset_agent_context(token: Token[AgentSkillContext | None]) -> None:
    _agent_context.reset(token)
