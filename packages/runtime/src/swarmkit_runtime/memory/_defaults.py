"""Memory by default (design/details/memory-by-default.md).

A workspace has memory unless it says otherwise. Two things make that true, both applied by the
resolver before anything downstream reads the workspace:

* the built-in ``memory-reader`` / ``memory-writer`` decision skills are bound automatically
  (reader before every agent, writer after) unless the workspace binds them itself — an explicit
  binding under ``governance.decision_skills`` is used exactly as written;
* the ``governed-memory`` and ``memory-reconcile`` reference skills are bundled here and injected
  when the workspace defines no skill with that id, so the curated store exists and the reconciler
  is wired. A workspace copy still wins.

``memory.enabled: false`` turns all of it off; combined with an explicit binding it is a resolution
error rather than a silent choice between the two.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime.workspace import DiscoveredArtifact

#: The bundled skill files — byte-identical copies of ``reference/skills/<id>.yaml`` (a test
#: enforces it), so "bundled" and "reference" never mean two different skills.
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
BUNDLED_SKILL_IDS: tuple[str, ...] = ("governed-memory", "memory-reconcile")

READER_DEFAULTS: dict[str, Any] = {
    "max_results": 5,
    "similarity_threshold": 0.15,
    "search_scope": "all",
}
WRITER_DEFAULTS: dict[str, Any] = {"min_output_length": 100}

MEMORY_READER = "memory-reader"
MEMORY_WRITER = "memory-writer"


@dataclass(frozen=True)
class MemoryConfig:
    """The effective ``memory`` block, defaults applied."""

    enabled: bool = True
    reader: dict[str, Any] = field(default_factory=lambda: dict(READER_DEFAULTS))
    writer: dict[str, Any] = field(default_factory=lambda: dict(WRITER_DEFAULTS))
    #: Which of the two bindings the workspace wrote itself (their config is theirs, not ours).
    explicit: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reader": dict(self.reader),
            "writer": dict(self.writer),
            "explicit": list(self.explicit),
        }


def memory_config(raw_workspace: dict[str, Any]) -> MemoryConfig:
    """Read the effective memory configuration off a raw workspace mapping."""
    block = raw_workspace.get("memory") or {}
    if not isinstance(block, dict):
        block = {}
    explicit = tuple(
        b["id"]
        for b in _decision_skills(raw_workspace)
        if isinstance(b, dict) and b.get("id") in (MEMORY_READER, MEMORY_WRITER)
    )
    return MemoryConfig(
        enabled=bool(block.get("enabled", True)),
        reader={**READER_DEFAULTS, **(block.get("reader") or {})},
        writer={**WRITER_DEFAULTS, **(block.get("writer") or {})},
        explicit=explicit,
    )


def _decision_skills(raw_workspace: dict[str, Any]) -> list[Any]:
    gov = raw_workspace.get("governance") or {}
    skills = gov.get("decision_skills") if isinstance(gov, dict) else None
    return list(skills) if isinstance(skills, list) else []


class MemoryDisabledButBound(ValueError):
    """``memory.enabled: false`` next to an explicit memory-reader/-writer binding."""


def apply_memory_defaults(raw_workspace: dict[str, Any]) -> dict[str, Any]:
    """Return *raw_workspace* with the auto-bindings added (a copy; the input is untouched).

    Raises :class:`MemoryDisabledButBound` when memory is off and the workspace still binds
    ``memory-reader`` or ``memory-writer`` — the two statements contradict, and picking one
    silently is how a workspace ends up remembering things its owner switched off.
    """
    cfg = memory_config(raw_workspace)
    if not cfg.enabled:
        if cfg.explicit:
            raise MemoryDisabledButBound(
                f"memory.enabled is false but governance.decision_skills binds "
                f"{', '.join(cfg.explicit)}; remove the binding(s) or enable memory"
            )
        return raw_workspace
    additions: list[dict[str, Any]] = []
    if MEMORY_READER not in cfg.explicit:
        additions.append(
            {
                "id": MEMORY_READER,
                "trigger": "pre_input",
                "scope": "*",
                "required": False,  # a memory read that can fail a run is worse than no memory
                "config": dict(cfg.reader),
            }
        )
    if MEMORY_WRITER not in cfg.explicit:
        additions.append(
            {
                "id": MEMORY_WRITER,
                "trigger": "post_output",
                "scope": "*",
                "required": False,
                "config": dict(cfg.writer),
            }
        )
    if not additions:
        return raw_workspace
    out = copy.deepcopy(dict(raw_workspace))
    gov = out.get("governance")
    if not isinstance(gov, dict):
        # No governance block means the mock allow-all provider (build_governance); saying so
        # explicitly keeps the model valid (`provider` is required) and the behaviour identical.
        gov = {"provider": "mock"}
        out["governance"] = gov
    existing = gov.get("decision_skills")
    gov["decision_skills"] = [*(existing if isinstance(existing, list) else []), *additions]
    return out


def bundled_memory_skills(present_skill_ids: set[str]) -> list[DiscoveredArtifact]:
    """The bundled ``governed-memory`` / ``memory-reconcile`` artifacts the workspace does not
    define itself, as discovery would have produced them."""
    out: list[DiscoveredArtifact] = []
    for skill_id in BUNDLED_SKILL_IDS:
        if skill_id in present_skill_ids:
            continue
        path = BUNDLED_SKILLS_DIR / f"{skill_id}.yaml"
        raw = yaml.safe_load(path.read_text()) or {}
        out.append(DiscoveredArtifact(path=path, kind="skill", raw=raw))
    return out
