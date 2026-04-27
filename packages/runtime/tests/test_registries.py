"""Tests for the skill + archetype registries (M1.4 / phase 3a + 3b)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from swael_runtime.archetypes import (
    ResolvedArchetype,
    build_archetype_registry,
)
from swael_runtime.skills import ResolvedSkill, build_skill_registry
from swael_runtime.workspace import discover

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID = FIXTURES / "workspaces"
INVALID = FIXTURES / "workspaces-invalid"


# ---- skill registry: happy paths ----------------------------------------


def test_skill_registry_builds_for_minimal_workspace() -> None:
    registry, errors = build_skill_registry(discover(VALID / "minimal"))
    assert registry == {}
    assert errors == []


def test_skill_registry_picks_up_every_skill_in_full() -> None:
    registry, errors = build_skill_registry(discover(VALID / "full"))
    assert errors == []
    assert set(registry.keys()) == {"audit-log-write"}
    assert isinstance(registry["audit-log-write"], ResolvedSkill)


def test_skill_registry_resolves_composed_chain() -> None:
    registry, errors = build_skill_registry(discover(VALID / "composed-skills"))
    assert errors == []
    assert set(registry.keys()) == {"judge-correctness", "judge-style", "panel"}
    panel = registry["panel"]
    names = tuple(r.id for r in panel.resolved_composes)
    assert names == ("judge-correctness", "judge-style")


def test_skill_registry_handles_grouped_skills() -> None:
    registry, errors = build_skill_registry(discover(VALID / "grouped-skills"))
    assert errors == []
    assert set(registry.keys()) == {"github-repo-read", "llm-judge"}


def test_resolved_skill_is_frozen() -> None:
    registry, _ = build_skill_registry(discover(VALID / "full"))
    skill = registry["audit-log-write"]
    try:
        skill.id = "nope"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("expected FrozenInstanceError")


# ---- skill registry: errors ---------------------------------------------


def test_skill_registry_flags_duplicate_ids() -> None:
    registry, errors = build_skill_registry(discover(INVALID / "duplicate-skill-id"))
    # Registry still has one copy (deterministic — first wins on sorted iter).
    assert set(registry.keys()) == {"same-id"}
    codes = [e.code for e in errors]
    assert codes == ["skill.duplicate-id"]
    assert errors[0].yaml_pointer == "/metadata/id"


def test_skill_registry_flags_composed_cycle() -> None:
    _, errors = build_skill_registry(discover(INVALID / "composed-cycle"))
    codes = [e.code for e in errors]
    assert "skill.composed-cycle" in codes
    cycle_err = next(e for e in errors if e.code == "skill.composed-cycle")
    assert "skill-a" in cycle_err.message
    assert "skill-b" in cycle_err.message


def test_skill_registry_flags_unknown_composed_reference() -> None:
    _, errors = build_skill_registry(discover(INVALID / "composed-unknown"))
    codes = [e.code for e in errors]
    assert codes == ["skill.composed-unknown"]
    assert "not-defined-anywhere" in errors[0].message


# ---- archetype registry: happy paths ------------------------------------


def test_archetype_registry_picks_up_every_archetype_in_full() -> None:
    artifacts = list(discover(VALID / "full"))
    skills, _ = build_skill_registry(artifacts)
    registry, errors = build_archetype_registry(artifacts, skills)
    assert errors == []
    assert set(registry.keys()) == {"supervisor-root"}
    assert isinstance(registry["supervisor-root"], ResolvedArchetype)


def test_archetype_registry_accepts_concrete_skill_reference() -> None:
    artifacts = list(discover(VALID / "with-archetypes"))
    skills, _ = build_skill_registry(artifacts)
    registry, errors = build_archetype_registry(artifacts, skills)
    assert errors == []
    assert "concrete-worker" in registry


def test_archetype_registry_accepts_abstract_placeholder() -> None:
    # Abstract placeholders must not require the named skill to exist —
    # their binding happens per-agent in M1.5 (phase 3c).
    artifacts = list(discover(VALID / "with-archetypes"))
    skills, _ = build_skill_registry(artifacts)
    registry, errors = build_archetype_registry(artifacts, skills)
    assert errors == []
    assert "abstract-worker" in registry


# ---- archetype registry: errors -----------------------------------------


def test_archetype_registry_flags_duplicate_ids() -> None:
    artifacts = list(discover(INVALID / "duplicate-archetype-id"))
    skills, _ = build_skill_registry(artifacts)
    registry, errors = build_archetype_registry(artifacts, skills)
    # First occurrence kept (deterministic).
    assert "same-archetype" in registry
    codes = [e.code for e in errors]
    assert codes == ["archetype.duplicate-id"]


def test_archetype_registry_flags_unknown_skill() -> None:
    artifacts = list(discover(INVALID / "archetype-unknown-skill"))
    skills, _ = build_skill_registry(artifacts)
    registry, errors = build_archetype_registry(artifacts, skills)
    codes = [e.code for e in errors]
    assert codes == ["archetype.unknown-skill"]
    assert "not-a-real-skill" in errors[0].message
    # Archetype is still registered so downstream can report more errors.
    assert "bad-worker" in registry
