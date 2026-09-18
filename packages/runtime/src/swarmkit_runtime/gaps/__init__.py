"""Skill gap log — tracks patterns of capability shortfall, on the persistence layer.

See ``design/details/decision-skills.md`` §Skill gap log.

Skill gaps are the input to the swarm growth cycle (design §12) —
they surface areas where the swarm needs new or improved skills.
The authoring AI reads them when suggesting new skills.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, insert, select, update

from swarmkit_runtime.gaps._tables import metadata, skill_gaps


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
    """The skill gap log on the runtime's persistence layer (the ``skill_gaps`` table of the
    ``runtime`` store kind — SQLite by default, Postgres when configured). A
    ``.swarmkit/gaps.jsonl`` written by an earlier version is imported on first open and renamed.

    Nothing wrote to this log from a run until 1.227.0 — `swarmkit gaps` read a file no code path
    filled. It is now fed by the tool loop: an agent that calls a tool it does not have has named
    a capability the workspace lacks, which is the input to the growth cycle (design §12).
    """

    def __init__(self, base_dir: Path, *, engine: Engine | None = None) -> None:
        from swarmkit_runtime.persistence import StoreKind, storage_for_workspace  # noqa: PLC0415
        from swarmkit_runtime.persistence._store import create_all_idempotent  # noqa: PLC0415

        self._root = Path(base_dir)
        self._engine = (
            engine
            if engine is not None
            else storage_for_workspace(self._root).engine(StoreKind.RUNTIME)
        )
        create_all_idempotent(metadata, self._engine)
        self._adopt_legacy_file()

    def _adopt_legacy_file(self) -> None:
        legacy = self._root / ".swarmkit" / "gaps.jsonl"
        if not legacy.exists():
            return
        for raw_line in legacy.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
                gap = _gap_from_dict(data)
            except (ValueError, KeyError):
                continue
            for _ in range(gap.occurrences):
                self.record(gap)
        legacy.rename(legacy.with_suffix(".jsonl.migrated"))

    def record(self, gap: SkillGap) -> None:
        """Append a gap entry. If an existing entry matches skill_id +
        topology_id + pattern, increment its occurrence count instead.
        """
        with self._engine.begin() as conn:
            where = (
                (skill_gaps.c.skill_id == gap.skill_id)
                & (skill_gaps.c.topology_id == gap.topology_id)
                & (skill_gaps.c.pattern == gap.pattern)
            )
            row = conn.execute(select(skill_gaps.c.occurrences).where(where)).first()
            if row is not None:
                conn.execute(update(skill_gaps).where(where).values(occurrences=int(row[0]) + 1))
                return
            conn.execute(
                insert(skill_gaps).values(
                    skill_id=gap.skill_id,
                    topology_id=gap.topology_id,
                    pattern=gap.pattern,
                    suggested_action=gap.suggested_action,
                    first_seen=gap.first_seen.isoformat(),
                    occurrences=gap.occurrences,
                )
            )

    def list_gaps(self) -> list[SkillGap]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(skill_gaps).order_by(skill_gaps.c.first_seen)).mappings().all()
            )
        return [_gap_from_dict(dict(r)) for r in rows]


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
