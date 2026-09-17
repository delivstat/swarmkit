"""Another agent as a skill — ``implementation.type: agent`` (design/details/a2a-interop.md).

One skill type, two resolutions: ``topology:`` runs a topology of this workspace as a child job of
the caller's run, in-process; ``card_url:`` reaches a remote agent through its A2A Agent Card,
with the A2A task API as the transport. Either way it is a *skill*: it goes through the same
permission seam as an MCP tool or a command, its call is audited like every other, and nothing
about "there is an agent on the other end" changes what the caller is allowed to do.

The pieces:

- :mod:`._context` — what a running call needs from the runtime (how to start a child run, the
  credential service, the review queue), installed per run as a context variable so the compiler
  does not have to thread it through every layer.
- :mod:`._governed` — the permission check, sibling of ``check_mcp_permission``.
- :mod:`._remote` — the A2A client: card resolution, ``message/send``, polling, cancel.
- :mod:`._executor` — the tool the model calls, and the ``on_unanswerable`` policies.
"""

from __future__ import annotations

from typing import Any

from ._context import AgentSkillContext, ChildRunOutcome, current_agent_context, set_agent_context
from ._spec import AgentSkillSpec, find_bad_agent_targets, parse_agent_spec

# The executor pulls in the compiler, which pulls in the resolver, which synthesizes the
# `pack:workspace` skills from this package — so the executor and the permission seam load on
# first use rather than at import, and this package stays importable from the resolver.
_LAZY = {
    "execute_agent_skill": ("._executor", "execute_agent_skill"),
    "check_agent_permission": ("._governed", "check_agent_permission"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module  # noqa: PLC0415

    return getattr(import_module(target[0], __name__), target[1])


__all__ = [
    "AgentSkillContext",
    "AgentSkillSpec",
    "ChildRunOutcome",
    "check_agent_permission",
    "current_agent_context",
    "execute_agent_skill",
    "find_bad_agent_targets",
    "parse_agent_spec",
    "set_agent_context",
]
