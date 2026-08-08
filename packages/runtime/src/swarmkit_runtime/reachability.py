"""Reachability — report configuration that no code path can reach (read-only).

``design/details/declared-but-unreachable.md``. Five defects in a row had one anatomy: declared,
accepted, validated, displayed, and loaded by nothing (bugs 21, 22, 23, 25). Each individual fix was
correct and none prevented the next.

The note's central claim is that this cannot be one check. Three classes, and only the first is
statically decidable:

* **A. broken at wiring** — the configuration never reaches its consumer. Bugs 22 and 25. The guard
  is ``False``, or the merge dropped the entry, so nothing is ever constructed.
  :func:`compute_reachability` catches this.
* **B. broken at selection** — wired, and the predicate that selects it is never true. Bug 23: the
  binding *was* attached to the node and ``b.trigger == "pre_input"`` was simply always ``False``.
  Nothing at compile time is wrong, so no static check can see it. :func:`compute_inert_bindings`
  answers it retrospectively, from the audit log.
* **C. broken at capability** — it ran and could not see what it needed (bug 21). Only a behavioural
  test catches that; it is out of scope here, named so the boundary is explicit.

The ledger is deliberately **not** a registry of "which code consumes which field". That is another
declaration that goes stale — and it would have been *wrong for all four bugs*, because it would
have said ``funnel`` is consumed by ``_compiler.py``, which is exactly what everyone believed. Here
the wiring sites record on the line where they wire, so the claim and the act are one statement: a
ledger entry cannot report a wrap that did not happen.

``swarmkit validate``, ``swarmkit serve``'s startup log and ``GET /workspace/reachability`` all call
:func:`compute_reachability` — one pure function, surfaced three times (Surface parity, as
``gate_coverage``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from swarmkit_runtime.resolver._resolved import ResolvedWorkspace

#: Funnel layers the runtime is expected to build when a funnel declares them. `approve` is here
#: even though the in-node binding defers it to the stage gate (see `build_advisory_approver`) —
#: it IS wired, by an approver that records and passes, and the ledger records that honestly.
FUNNEL_LAYERS: tuple[str, ...] = ("validate", "judge", "review", "approve")


@dataclass(frozen=True)
class Declaration:
    """One configured thing that some code path is expected to consume.

    ``key`` is what the ledger records against, so a declaration and its wiring can be matched
    without either side knowing the other's internals.
    """

    kind: str  # "decision_skill" | "funnel" | "funnel_layer"
    key: str
    #: where an operator would go to change it — a topology id, an agent id, a funnel id
    declared_on: str
    #: rendered in the report; keeps "REQUIRED" visible without the caller re-deriving it
    required: bool = False
    detail: str = ""


@dataclass
class WiringLedger:
    """What the compiler actually built, recorded by the code that built it.

    A ledger call belongs INSIDE the branch that does the wiring, on the line where it wires. That
    is the whole mechanism: the claim and the act are the same statement, so this cannot report a
    wrap that did not happen. Under bug 25 the branch never ran, the ledger stayed empty, and the
    diff would have named the funnel.

    A new wiring site that forgets its call produces a false *unreachable* — noisy rather than
    silent, which is the opposite of the failure this exists to prevent.
    """

    entries: set[tuple[str, str]] = field(default_factory=set)

    def wired(self, kind: str, key: str) -> None:
        self.entries.add((kind, key))

    def has(self, kind: str, key: str) -> bool:
        return (kind, key) in self.entries


@dataclass(frozen=True)
class Unreachable:
    """A declaration nothing wired."""

    declaration: Declaration

    def line(self) -> str:
        req = "  REQUIRED" if self.declaration.required else ""
        detail = f" — {self.declaration.detail}" if self.declaration.detail else ""
        return (
            f"{self.declaration.kind} {self.declaration.key!r} "
            f"declared on {self.declaration.declared_on}{detail}: nothing wired it{req}"
        )


@dataclass(frozen=True)
class ReachabilityReport:
    """Every declaration, split by whether a code path was found for it."""

    reachable: tuple[Declaration, ...]
    unreachable: tuple[Unreachable, ...]

    @property
    def ok(self) -> bool:
        return not self.unreachable

    @property
    def blocking(self) -> tuple[Unreachable, ...]:
        """The unreachable declarations a workspace is arguably broken by.

        A `required: true` binding that nothing runs is the worst case in this whole family: the
        workspace believes a check is enforcing something. `--require` gates on these.
        """
        return tuple(u for u in self.unreachable if u.declaration.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reachable": [
                {
                    "kind": d.kind,
                    "key": d.key,
                    "declared_on": d.declared_on,
                    "required": d.required,
                }
                for d in self.reachable
            ],
            "unreachable": [
                {
                    "kind": u.declaration.kind,
                    "key": u.declaration.key,
                    "declared_on": u.declaration.declared_on,
                    "required": u.declaration.required,
                    "detail": u.declaration.detail,
                    "message": u.line(),
                }
                for u in self.unreachable
            ],
        }


# ---- enumerating what was declared ---------------------------------------------------------------


def declarations_for_topology(workspace: ResolvedWorkspace, topology_id: str) -> list[Declaration]:
    """Everything the runtime is expected to build for one topology.

    Read from the RESOLVED workspace, deliberately independent of any consumer — the point is to
    have a second, non-consuming opinion about what exists.

    Scoped to the two families that have actually burned us. A check that reports fifty
    uninteresting things is a check nobody reads, which is the failure mode of the thing it
    replaces. New kinds join here.
    """
    topology = workspace.topologies.get(topology_id)
    if topology is None:
        return []

    out: list[Declaration] = []
    for agent in _walk(topology.root):
        funnel = getattr(agent, "funnel", None)
        if funnel is None:
            continue
        out.append(
            Declaration(
                kind="funnel",
                key=f"{agent.id}:{funnel.id}",
                declared_on=f"agent {agent.id}",
                detail=f"funnel {funnel.id}",
            )
        )
        spec = dict(getattr(funnel, "spec", {}) or {})
        out.extend(
            Declaration(
                kind="funnel_layer",
                key=f"{agent.id}:{funnel.id}:{layer}",
                declared_on=f"funnel {funnel.id}",
                detail=f"the {layer} layer",
            )
            for layer in FUNNEL_LAYERS
            if layer in spec
        )
    return out


def declarations_for_bindings(bindings: Iterable[Any], topology_id: str) -> list[Declaration]:
    """Decision-skill bindings, enumerated from the MERGED list.

    Merged, not raw, on purpose: bug 22 was the merge dropping advisory bindings, and a check that
    enumerated the raw workspace would have caught it while a check that enumerated the merged list
    would not. The merge is covered by its own tests; what this asks is the next question — the
    binding survived the merge, did anything wire it?
    """
    return [
        Declaration(
            kind="decision_skill",
            key=f"{topology_id}:{b.id}:{b.trigger}",
            declared_on=f"topology {topology_id}",
            required=bool(getattr(b, "required", False)),
            detail=f"at {b.trigger}",
        )
        for b in bindings
    ]


def _walk(agent: Any) -> list[Any]:
    out = [agent]
    for child in getattr(agent, "children", ()) or ():
        out.extend(_walk(child))
    return out


# ---- the diff ------------------------------------------------------------------------------------


def compute_reachability(
    declarations: Iterable[Declaration], ledger: WiringLedger
) -> ReachabilityReport:
    """Split declarations by whether the ledger recorded a code path for each.

    Pure: the caller supplies both sides, so the CLI, the server and a test all ask the same
    question of the same data.
    """
    reachable: list[Declaration] = []
    unreachable: list[Unreachable] = []
    for decl in declarations:
        if ledger.has(decl.kind, decl.key):
            reachable.append(decl)
        else:
            unreachable.append(Unreachable(decl))
    return ReachabilityReport(tuple(reachable), tuple(unreachable))


# ---- class B: wired, and never once fired --------------------------------------------------------


@dataclass(frozen=True)
class InertBinding:
    """A binding that was wired and has produced no evaluation across N applicable runs."""

    skill_id: str
    trigger: str
    topologies: tuple[str, ...]
    evaluations: int
    applicable_runs: int
    required: bool

    def line(self) -> str:
        where = ", ".join(self.topologies) or "-"
        req = "  REQUIRED" if self.required else ""
        return (
            f"{self.skill_id:<24} {self.trigger:<14} bound on {where:<24} "
            f"{self.evaluations} evaluations / {self.applicable_runs} applicable runs{req}"
        )


def compute_inert_bindings(
    bindings_by_topology: Mapping[str, Iterable[Any]],
    evaluations: Mapping[tuple[str, str], int],
    runs_by_topology: Mapping[str, int],
) -> tuple[InertBinding, ...]:
    """Bindings with zero evaluations, reported against a denominator.

    "Zero evaluations" means nothing on its own — it is indistinguishable from a trigger point the
    runs never reached. Bug 25's report got this right by hand, using ``memory context injected: 2``
    as the control that made ``spec-judge: 0`` a real negative rather than a logging gap. The
    denominator here is completed runs of the topologies a binding applies to; a binding with no
    applicable runs is not reported, because nothing has been learned about it yet.

    ``required: true`` at 0/N is the loudest line the system can print, and the line bug 23 would
    have produced months before anyone noticed.
    """
    seen: dict[tuple[str, str], InertBinding] = {}
    for topology_id, bindings in bindings_by_topology.items():
        for b in bindings:
            key = (str(b.id), str(b.trigger))
            prior = seen.get(key)
            topologies = (*(prior.topologies if prior else ()), topology_id)
            seen[key] = InertBinding(
                skill_id=key[0],
                trigger=key[1],
                topologies=topologies,
                evaluations=evaluations.get(key, 0),
                applicable_runs=(prior.applicable_runs if prior else 0)
                + runs_by_topology.get(topology_id, 0),
                required=bool(getattr(b, "required", False)) or bool(prior and prior.required),
            )
    return tuple(
        b
        for b in sorted(seen.values(), key=lambda x: (not x.required, x.skill_id))
        if b.evaluations == 0 and b.applicable_runs > 0
    )
