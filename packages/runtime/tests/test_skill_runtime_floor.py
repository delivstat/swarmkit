"""A skill may declare the runtime it needs, and is refused at load when it is not met.

A skill is portable data that can outlive the runtime that installed it. `command` backing arrived
in 1.197.0, `requires:` ordering in 1.193.0, declared `effects` under a `readonly` MCP server in
1.199.0. Without a floor, such a skill resolves cleanly into an older workspace and fails much later
with an error naming nothing useful.

That is the `swarmkit-webui` failure in a different costume — a separately-versioned artifact with
an unbounded floor, resolvable and broken, found by a user rather than a check. These tests pin the
refusal, and equally pin the two cases where refusing would be wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from swarmkit_runtime.skills import ResolvedSkill
from swarmkit_runtime.skills._runtime_floor import unmet_floors
from swarmkit_schema.models import SwarmKitSkill


def _skill(floor: str | None = None, skill_id: str = "s") -> ResolvedSkill:
    raw: dict[str, Any] = {
        "apiVersion": "swarmkit/v1",
        "kind": "Skill",
        "metadata": {"id": skill_id, "name": "S", "description": "A skill under test."},
        "category": "capability",
        "implementation": {"type": "llm_prompt", "prompt": "hi"},
        "provenance": {"authored_by": "human", "version": "1.0.0"},
    }
    if floor:
        raw["provenance"]["requires_runtime"] = floor
    return ResolvedSkill(
        id=skill_id, raw=SwarmKitSkill.model_validate(raw), source_path=Path(f"{skill_id}.yaml")
    )


class TestTheFloorIsEnforced:
    def test_a_runtime_below_the_floor_is_refused(self) -> None:
        errors = unmet_floors({"s": _skill(">=1.250.0")}, current="1.201.0")
        assert len(errors) == 1
        assert errors[0].code == "skill.runtime-too-old"

    def test_the_message_names_both_versions(self) -> None:
        """ "Incompatible" without the two numbers sends the reader to the source to find out
        what it wanted."""
        message = unmet_floors({"s": _skill(">=1.250.0")}, current="1.201.0")[0].message
        assert "1.250.0" in message
        assert "1.201.0" in message

    def test_the_suggestion_says_what_to_do(self) -> None:
        suggestion = unmet_floors({"s": _skill(">=1.250.0")}, current="1.201.0")[0].suggestion or ""
        assert "swarmkit-runtime>=1.250.0" in suggestion

    @pytest.mark.parametrize("current", ["1.197.0", "1.201.0", "2.0.0"])
    def test_a_runtime_at_or_above_the_floor_is_accepted(self, current: str) -> None:
        assert unmet_floors({"s": _skill(">=1.197.0")}, current=current) == []

    def test_every_failing_skill_is_reported_not_just_the_first(self) -> None:
        """One upgrade should resolve all of them; reporting one at a time is N restarts."""
        skills = {
            "a": _skill(">=1.250.0", "a"),
            "b": _skill(">=1.300.0", "b"),
            "c": _skill(">=1.100.0", "c"),
        }
        errors = unmet_floors(skills, current="1.201.0")
        assert {e.message.split("'")[1] for e in errors} == {"a", "b"}


class TestWhereRefusingWouldBeWrong:
    def test_no_floor_means_no_constraint(self) -> None:
        """Every skill written before this field existed."""
        assert unmet_floors({"s": _skill()}, current="1.0.0") == []

    def test_an_unreadable_runtime_version_skips_rather_than_fails(self) -> None:
        """A source checkout has no installed metadata. Refusing every skill because we cannot
        introspect ourselves would break the development case to protect the deployed one."""
        assert unmet_floors({"s": _skill(">=9.0.0")}, current="") == []

    def test_a_prerelease_runtime_compares_on_its_numeric_prefix(self) -> None:
        """`1.201.0rc1` meeting a `>=1.197.0` floor is not a reason to refuse: a pre-release of a
        version that satisfies the floor satisfies it."""
        assert unmet_floors({"s": _skill(">=1.197.0")}, current="1.201.0rc1") == []


class TestTheSchemaIsTheFirstGuard:
    """The pattern rejects a malformed floor before any code sees it, so `unmet_floors` never has
    to interpret a range or a pin."""

    @pytest.mark.parametrize("bad", [">=1.197.0,<2.0.0", "1.197.0", "^1.197", ">1.197.0", "latest"])
    def test_only_a_floor_validates(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            _skill(bad)

    def test_a_floor_validates(self) -> None:
        assert _skill(">=1.197.0") is not None
