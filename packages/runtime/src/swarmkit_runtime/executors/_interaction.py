"""The interaction-driver seam (executor-relay-plan.md, RFC §6.2).

`relay` — pausing a harness for a mid-run approval and feeding the decision back into the running
session — needs **bidirectional session control** that the one-way declarative stream can't express.
That single bidirectional part is a per-harness ``InteractionDriver`` (Tier-1 code); everything else
(the approval inbox, policy consult, scoping, audit, never-hang) is generic core.

An adapter *declares* its driver in data (``interaction.driver``); a harness with no driver leaves
``relay`` unavailable and the run falls back to ``abort`` (never-hang preserved). Concrete drivers —
``hold-stream`` (live stdin) and ``park-resume`` (checkpoint + relaunch) — land in later slices.
"""

from __future__ import annotations

from swarmkit_runtime.executors._events import ExecApprovalRequested


class InteractionDriver:
    """Feeds an approval decision back into a running harness session.

    ``supports_relay`` gates the whole relay path: when ``False`` (the default / no driver), the
    orchestrator never engages and an out-of-grant request aborts. A concrete driver sets it
    ``True`` and implements :meth:`respond`.
    """

    supports_relay: bool = False

    async def respond(self, request: ExecApprovalRequested, *, granted: bool) -> None:
        """Deliver the decision for ``request`` to the live session. Default: not supported."""
        raise NotImplementedError("this executor kind does not support relay interaction")


class NoInteractionDriver(InteractionDriver):
    """The default: no bidirectional control, so ``relay`` degrades to ``abort``."""

    supports_relay = False
