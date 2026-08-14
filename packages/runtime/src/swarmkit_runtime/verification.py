"""Verification strength — how strongly is an agent's output checked (read-only).

`design/details/extracting-the-pipeline.md`. `gate_coverage` answered "what is the narrowest
verified edge of this pipeline" by classifying stage edges against their funnels. The stage graph
left with the bundled sequencer, so the edge analysis went with it — but the *question underneath*
was never about pipelines. **Which agents produce an artifact, and how strongly is it checked** is
about topologies and funnels, both of which stay, and it is the natural sibling of the reachability
report: *"this run's output is verified by nothing"* is the same class of finding as *"this binding
is reached by nothing"*.

**Strength counts WIRED layers, not declared ones.** A funnel declaring `validate` whose builder
returned None contributes nothing, because it does nothing — and counting it would make this check
repeat the exact defect the reachability report exists to catch. The wiring ledger is the same one
`compute_reachability` uses, from the same compile.

**Only the root is a finding.** Every agent's strength is reported, but a leaf worker returning a
fact to its parent is not producing a reviewable artifact, and flagging every one would make a
report nobody reads — which is how the thing this replaces failed. The root's output IS the run's
output: what a caller acts on, what a gate approves, what a downstream stage would have consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from swarmkit_runtime.reachability import WiringLedger
    from swarmkit_runtime.resolver._resolved import ResolvedWorkspace

#: The advisory layers, weakest → strongest, that check an artifact before a human sees it.
PRE_FILTERS: tuple[str, ...] = ("validate", "judge", "review")


@dataclass(frozen=True)
class AgentVerification:
    """How one agent's output is checked."""

    topology_id: str
    agent_id: str
    is_root: bool
    funnel_id: str = ""
    #: layers that are declared AND wired — what actually runs
    layers: tuple[str, ...] = ()
    #: declared on the funnel, whether or not anything built them
    declared: tuple[str, ...] = ()

    @property
    def strength(self) -> int:
        """0 when nothing checks this output; otherwise one point per wired pre-filter, plus one
        for a human approve layer that actually opens a gate."""
        return len(self.layers)

    @property
    def verified(self) -> bool:
        return self.strength > 0

    @property
    def inert(self) -> tuple[str, ...]:
        """Declared and not wired — reported because a reader counting declared layers would
        over-state how checked this output is."""
        return tuple(d for d in self.declared if d not in self.layers)

    def line(self) -> str:
        where = f"{self.topology_id}/{self.agent_id}"
        root = " (root)" if self.is_root else ""
        if not self.funnel_id:
            return f"{where}{root}: no funnel — its output is checked by nothing"
        layers = ", ".join(self.layers) if self.layers else "nothing wired"
        inert = f"; declared but inert: {', '.join(self.inert)}" if self.inert else ""
        return f"{where}{root}: funnel {self.funnel_id} — {layers}{inert}"


@dataclass(frozen=True)
class VerificationReport:
    """Every agent's verification strength, and the roots that have none."""

    agents: tuple[AgentVerification, ...]

    @property
    def unverified_roots(self) -> tuple[AgentVerification, ...]:
        """Roots whose output nothing checks — the finding `--require-verified` gates on."""
        return tuple(a for a in self.agents if a.is_root and not a.verified)

    @property
    def weakest(self) -> AgentVerification | None:
        """The least-checked root: the honest headline, as "narrowest verified edge" was."""
        roots = [a for a in self.agents if a.is_root]
        return min(roots, key=lambda a: a.strength) if roots else None

    @property
    def ok(self) -> bool:
        return not self.unverified_roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "agents": [
                {
                    "topology_id": a.topology_id,
                    "agent_id": a.agent_id,
                    "is_root": a.is_root,
                    "funnel_id": a.funnel_id,
                    "layers": list(a.layers),
                    "declared": list(a.declared),
                    "inert": list(a.inert),
                    "strength": a.strength,
                    "verified": a.verified,
                }
                for a in self.agents
            ],
            "unverified_roots": [a.line() for a in self.unverified_roots],
        }


def _walk(agent: Any) -> list[Any]:
    out = [agent]
    for child in getattr(agent, "children", ()) or ():
        out.extend(_walk(child))
    return out


def compute_verification(workspace: ResolvedWorkspace, ledger: WiringLedger) -> VerificationReport:
    """Classify every agent by how strongly its output is checked.

    Pure: the caller supplies the workspace and the ledger from its own compile, so `swarmkit
    validate`, a server route and a test all ask one question of one implementation.
    """
    agents: list[AgentVerification] = []
    for topology_id in sorted(workspace.topologies):
        topology = workspace.topologies[topology_id]
        root_id = topology.root.id
        for agent in _walk(topology.root):
            funnel = getattr(agent, "funnel", None)
            if funnel is None:
                agents.append(AgentVerification(topology_id, agent.id, is_root=agent.id == root_id))
                continue
            spec = dict(getattr(funnel, "spec", {}) or {})
            declared = tuple(k for k in (*PRE_FILTERS, "approve") if k in spec)
            wired = tuple(
                layer
                for layer in declared
                if ledger.has("funnel_layer", f"{agent.id}:{funnel.id}:{layer}")
            )
            agents.append(
                AgentVerification(
                    topology_id=topology_id,
                    agent_id=agent.id,
                    is_root=agent.id == root_id,
                    funnel_id=funnel.id,
                    layers=wired,
                    declared=declared,
                )
            )
    return VerificationReport(tuple(agents))


__all__ = [
    "PRE_FILTERS",
    "AgentVerification",
    "VerificationReport",
    "compute_verification",
]
