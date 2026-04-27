"""Frozen dataclasses that make up a ResolvedWorkspace.

Public names are re-exported from :mod:`swael_runtime.resolver`; keep
this file focused on the data shapes themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from swael_schema.models import SwarmKitTopology, SwarmKitTrigger, SwarmKitWorkspace

from swael_runtime.archetypes import ResolvedArchetype
from swael_runtime.skills import ResolvedSkill

AgentRole = Literal["root", "leader", "worker"]


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
    children: tuple[ResolvedAgent, ...] = field(default_factory=tuple)
    source_archetype: str | None = None


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


__all__ = [
    "AgentRole",
    "ResolvedAgent",
    "ResolvedTopology",
    "ResolvedTrigger",
    "ResolvedWorkspace",
]
