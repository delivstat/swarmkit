"""Tests for the skill gap log (M4).

See ``design/details/decision-skills.md`` §Skill gap log.
"""

from __future__ import annotations

from pathlib import Path

from swael_runtime.gaps import SkillGapLog, create_skill_gap


def test_record_and_list(tmp_path: Path) -> None:
    log = SkillGapLog(tmp_path)
    gap = create_skill_gap(
        skill_id="code-quality-review",
        topology_id="review",
        pattern="confidence < 0.5 on 3 consecutive runs",
        suggested_action="consider adding a specialized judge",
    )
    log.record(gap)

    gaps = log.list_gaps()
    assert len(gaps) == 1
    assert gaps[0].skill_id == "code-quality-review"
    assert gaps[0].pattern == "confidence < 0.5 on 3 consecutive runs"
    assert gaps[0].occurrences == 1


def test_duplicate_increments_occurrences(tmp_path: Path) -> None:
    log = SkillGapLog(tmp_path)
    gap = create_skill_gap(
        skill_id="security-scan",
        topology_id="review",
        pattern="verdict=fail 5 times in a row",
        suggested_action="review the scan rubric",
    )
    log.record(gap)
    log.record(gap)
    log.record(gap)

    gaps = log.list_gaps()
    assert len(gaps) == 1
    assert gaps[0].occurrences == 3


def test_different_patterns_separate_entries(tmp_path: Path) -> None:
    log = SkillGapLog(tmp_path)
    log.record(
        create_skill_gap(
            skill_id="s1",
            topology_id="t1",
            pattern="pattern-a",
            suggested_action="action-a",
        )
    )
    log.record(
        create_skill_gap(
            skill_id="s1",
            topology_id="t1",
            pattern="pattern-b",
            suggested_action="action-b",
        )
    )

    gaps = log.list_gaps()
    assert len(gaps) == 2


def test_empty_log(tmp_path: Path) -> None:
    log = SkillGapLog(tmp_path)
    assert log.list_gaps() == []


def test_persistence_across_instances(tmp_path: Path) -> None:
    log1 = SkillGapLog(tmp_path)
    log1.record(
        create_skill_gap(
            skill_id="s1",
            topology_id="t1",
            pattern="p1",
            suggested_action="a1",
        )
    )

    log2 = SkillGapLog(tmp_path)
    gaps = log2.list_gaps()
    assert len(gaps) == 1
    assert gaps[0].skill_id == "s1"
