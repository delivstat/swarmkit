"""Which skills this agent has already called successfully, and what that unlocks.

`design/details/skill-prerequisites.md`. A topology can declare that one skill call is a
precondition of another::

    skills: [list-build-conventions, get-build-convention]
    requires:
      get-build-convention: [list-build-conventions]

The evidence for enforcing rather than asking is a controlled comparison: in a **single run, same
agent, same prompt**, an ack-gated tool was called 4 times and a merely-requested one 0 times. The
variable is not the prompt; it is whether the tool refuses service. So ordering has to be mechanism.

**Per `(run, agent)`, not per run.** A prerequisite is about what is in *this* agent's context — if
agent A read the card, agent B did not, and a run-scoped set would let a parallel sibling satisfy a
prerequisite it never saw.

**A module-level ledger, not a `ContextVar`.** The harness gateway serves its tool calls on
uvicorn's tasks, which do not inherit the run's context — the gateway already captures the run id at
registration for exactly this reason. A `ContextVar` would enforce on the model path and silently
never fire on the harness path, which is the failure mode this repo keeps finding.

**The refusal message is the mechanism.** A generic "permission denied" invites give-up or thrash.
:func:`refusal` names what is missing and what to do, so the agent recovers inside its own loop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: ``{(run_id, agent_id): {skill_id, ...}}`` — skills that have returned successfully.
_satisfied: dict[tuple[str, str], set[str]] = {}

Requires = Mapping[str, Sequence[str]]


def note_satisfied(*, run_id: str | None, agent_id: str, skill_id: str) -> None:
    """Record that ``skill_id`` returned successfully for this ``(run, agent)``.

    Called only where the result actually reached the agent — an exception or an MCP ``isError``
    result does not satisfy a prerequisite, because nothing was learned.
    """
    if not skill_id:
        return
    _satisfied.setdefault((run_id or "", agent_id), set()).add(skill_id)


def missing(
    requires: Requires | None, *, run_id: str | None, agent_id: str, skill_id: str
) -> tuple[str, ...]:
    """The prerequisites of ``skill_id`` not yet satisfied for this ``(run, agent)``.

    Empty when the skill is unguarded or every prerequisite has been called. ``requires: [a, b]``
    means both, in any order — peers are order-independent.
    """
    if not requires or not skill_id:
        return ()
    needed = requires.get(skill_id)
    if not needed:
        return ()
    have = _satisfied.get((run_id or "", agent_id), set())
    return tuple(dict.fromkeys(n for n in needed if n not in have))


def refusal(skill_id: str, unmet: Sequence[str]) -> str:
    """The actionable refusal. Specified and tested rather than left to implementation, because
    being actionable is the part doing the work."""
    names = ", ".join(unmet)
    verb = "which has" if len(unmet) == 1 else "which have"
    return (
        f"{skill_id} requires {names}, {verb} not been called in this session. "
        f"Call {names} first, then retry."
    )


def forget_run(run_id: str | None) -> None:
    """Drop a finished run's ledger. Without this a long-lived ``swarmkit serve`` accumulates one
    entry per (run, agent) forever."""
    for key in [k for k in _satisfied if k[0] == (run_id or "")]:
        del _satisfied[key]


def satisfied_for(run_id: str | None, agent_id: str) -> frozenset[str]:
    """What this ``(run, agent)`` has called successfully. For tests and introspection."""
    return frozenset(_satisfied.get((run_id or "", agent_id), set()))


__all__ = [
    "Requires",
    "forget_run",
    "missing",
    "note_satisfied",
    "refusal",
    "satisfied_for",
]
