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

from ._context import AgentSkillContext, ChildRunOutcome, current_agent_context, set_agent_context
from ._executor import execute_agent_skill
from ._governed import check_agent_permission
from ._spec import AgentSkillSpec, find_bad_agent_targets, parse_agent_spec

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
