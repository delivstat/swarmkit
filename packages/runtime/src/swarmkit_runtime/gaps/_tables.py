"""SQLAlchemy Core table for the skill gap log (design §12, decision-skills.md §Skill gap log).

One row per ``(skill_id, topology_id, pattern)``; ``occurrences`` counts repeats. Lives on the
``runtime`` store kind — the workspace's configured database, SQLite or Postgres — where it was a
JSONL file under ``.swarmkit/`` before 1.227.0.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, Table, Text

metadata = MetaData()

skill_gaps = Table(
    "skill_gaps",
    metadata,
    Column("skill_id", Text, primary_key=True),
    Column("topology_id", Text, primary_key=True),
    Column("pattern", Text, primary_key=True),
    Column("suggested_action", Text, nullable=False, default=""),
    Column("first_seen", Text, nullable=False),
    Column("occurrences", Integer, nullable=False, default=1),
)
