"""SQLAlchemy Core table definition for the audit log.

One ``Table`` drives both SQLite and Postgres (design/details/postgres-backend.md). Columns +
indexes match the original hand-written schema exactly, so existing SQLite audit DBs keep working.
Append-only by design (§8.3): no UPDATE and only a retention DELETE are ever issued.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, Index, Integer, MetaData, Table, Text

audit_metadata = MetaData()

audit_events = Table(
    "audit_events",
    audit_metadata,
    Column("event_id", Text, primary_key=True),
    Column("event_type", Text, nullable=False),
    Column("agent_id", Text, nullable=False),
    Column("timestamp", Text, nullable=False),
    Column("run_id", Text),
    Column("parent_event_id", Text),
    Column("topology_id", Text),
    Column("skill_id", Text),
    Column("agent_role", Text),
    Column("skill_category", Text),
    Column("inputs", Text),
    Column("outputs", Text),
    Column("verdict", Text),
    Column("reasoning", Text),
    Column("confidence", Float),
    Column("model_provider", Text),
    Column("model_name", Text),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("cost_usd", Float),
    Column("duration_ms", Integer),
    Column("policy_decision", Text),
    Column("policy_reason", Text),
    Column("error", Text),
    Column("payload", Text),
    Index("idx_audit_run_id", "run_id"),
    Index("idx_audit_agent_id", "agent_id"),
    Index("idx_audit_timestamp", "timestamp"),
    Index("idx_audit_event_type", "event_type"),
)
