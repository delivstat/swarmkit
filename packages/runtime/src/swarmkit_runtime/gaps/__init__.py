"""Skill gap log — tracks patterns of capability shortfall.

See ``design/details/decision-skills.md`` §Skill gap log.

Skill gaps are the input to the swarm growth cycle (design §12) —
they surface areas where the swarm needs new or improved skills.
The authoring AI reads them when suggesting new skills.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillGap:
    """A recorded pattern of capability shortfall."""

    skill_id: str
    topology_id: str
    pattern: str
    suggested_action: str
    first_seen: datetime
    occurrences: int = 1


class SkillGapLog:
    """JSONL-backed skill gap log under ``.swarmkit/gaps.jsonl``."""

    def __init__(self, base_dir: Path) -> None:
        self._dir = base_dir / ".swarmkit"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "gaps.jsonl"

    def record(self, gap: SkillGap) -> None:
        """Append a gap entry. If an existing entry matches skill_id +
        topology_id + pattern, increment its occurrence count instead.
        """
        existing = self._load_all()
        key = (gap.skill_id, gap.topology_id, gap.pattern)

        for _i, entry in enumerate(existing):
            if (entry["skill_id"], entry["topology_id"], entry["pattern"]) == key:
                entry["occurrences"] = entry.get("occurrences", 1) + 1
                self._write_all(existing)
                return

        data = asdict(gap)
        data["first_seen"] = gap.first_seen.isoformat()
        existing.append(data)
        self._write_all(existing)

    def list_gaps(self) -> list[SkillGap]:
        return [_gap_from_dict(d) for d in self._load_all()]

    def _load_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for raw_line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped:
                entries.append(json.loads(stripped))
        return entries

    def _write_all(self, entries: list[dict[str, Any]]) -> None:
        lines = [json.dumps(e) for e in entries]
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_skill_gap(
    *,
    skill_id: str,
    topology_id: str,
    pattern: str,
    suggested_action: str,
) -> SkillGap:
    """Factory with auto-generated timestamp."""
    return SkillGap(
        skill_id=skill_id,
        topology_id=topology_id,
        pattern=pattern,
        suggested_action=suggested_action,
        first_seen=datetime.now(tz=UTC),
    )


def _gap_from_dict(data: dict[str, Any]) -> SkillGap:
    return SkillGap(
        skill_id=data["skill_id"],
        topology_id=data["topology_id"],
        pattern=data["pattern"],
        suggested_action=data["suggested_action"],
        first_seen=datetime.fromisoformat(data["first_seen"]),
        occurrences=data.get("occurrences", 1),
    )


__all__ = [
    "SkillGap",
    "SkillGapLog",
    "create_skill_gap",
]
