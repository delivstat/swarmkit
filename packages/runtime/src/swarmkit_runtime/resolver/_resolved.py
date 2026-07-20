"""Frozen dataclasses that make up a ResolvedWorkspace.

Public names are re-exported from :mod:`swarmkit_runtime.resolver`; keep
this file focused on the data shapes themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from swarmkit_schema.models import (
    SwarmKitFunnel,
    SwarmKitTopology,
    SwarmKitTrigger,
    SwarmKitWorkspace,
)

from swarmkit_runtime.archetypes import ResolvedArchetype
from swarmkit_runtime.executors import ResolvedExecutor
from swarmkit_runtime.skills import ResolvedSkill

AgentRole = Literal["root", "leader", "worker"]


@dataclass(frozen=True)
class ResolvedFunnel:
    """A Funnel artifact, schema-validated and ready for the compiler.

    A funnel is a reusable per-artifact quality gate (design/details/gate-funnel.md):
    ``validate -> judge -> (review) -> approve``. ``spec`` is the schema-validated raw
    mapping the compiler reads to build the gate subgraph; ``raw`` is the typed model.
    """

    id: str
    raw: SwarmKitFunnel
    source_path: Path
    spec: Mapping[str, Any]


@dataclass(frozen=True)
class ResolvedAgent:
    """An agent with every reference resolved and every inherited field merged.

    - ``model`` / ``prompt`` / ``iam`` are merged per the precedence rules in
      ``design/details/topology-loader.md`` phase 3c (archetype loses to
      agent-level overrides; shallow merge at the top of each block).
    - ``skills`` is a tuple of :class:`ResolvedSkill`; every reference is a
      concrete skill in the workspace registry. Abstract placeholders in the
      archetype have been bound to concrete skills here.
    - ``children`` is the recursive agent tree beneath this one.
    """

    id: str
    role: AgentRole
    model: Mapping[str, Any] | None
    prompt: Mapping[str, Any] | None
    skills: tuple[ResolvedSkill, ...]
    iam: Mapping[str, Any] | None
    output_schema: Mapping[str, Any] | None = None
    output_schema_disabled: bool = False
    # Optional per-artifact quality gate on this agent's output (design/details/gate-funnel.md).
    # Resolved from the node's ``funnel: <id>`` reference against the workspace funnel registry;
    # the compiler wraps the node in a gate subgraph (validate -> judge -> approve) when present.
    funnel: ResolvedFunnel | None = None
    children: tuple[ResolvedAgent, ...] = field(default_factory=tuple)
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    source_archetype: str | None = None
    # How this agent's node executes (design executor-abstraction). From its archetype's executor
    # block; defaults to `model` (today's behavior). The compiler dispatches on `executor.kind`.
    executor: ResolvedExecutor = field(default_factory=lambda: ResolvedExecutor(kind="model"))


@dataclass(frozen=True)
class ResolvedTopology:
    """A topology with its agent tree fully resolved."""

    id: str
    raw: SwarmKitTopology
    source_path: Path
    root: ResolvedAgent


@dataclass(frozen=True)
class ResolvedTrigger:
    """A trigger whose ``targets`` have been verified against the topology
    registry.
    """

    id: str
    raw: SwarmKitTrigger
    source_path: Path
    targets: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedWorkspace:
    """The top-level typed artifact the compiler and CLI consume."""

    raw: SwarmKitWorkspace
    source_path: Path
    topologies: Mapping[str, ResolvedTopology]
    skills: Mapping[str, ResolvedSkill]
    archetypes: Mapping[str, ResolvedArchetype]
    triggers: Sequence[ResolvedTrigger]
    funnels: Mapping[str, ResolvedFunnel] = field(default_factory=dict)


__all__ = [
    "AgentRole",
    "ResolvedAgent",
    "ResolvedFunnel",
    "ResolvedTopology",
    "ResolvedTrigger",
    "ResolvedWorkspace",
]
