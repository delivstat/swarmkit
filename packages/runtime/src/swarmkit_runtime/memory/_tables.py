"""SQLAlchemy Core table for workspace memory (design/details/workspace-memory.md).

One table, one row per remembered turn. Lives on the ``memory`` store kind — the same engine the
governed-memory tables use — so a workspace that configures Postgres keeps its memory there, and a
SQLite workspace keeps it in ``.swarmkit/store.sqlite`` next to everything else. Before 1.227.0 this
was a JSON file under ``.swarmkit/``, which the storage service could not move.

Own ``MetaData`` so it can create-all independently; lists ride as JSON ``Text``, matching the
persistence store's dialect-agnostic convention.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, Text

metadata = MetaData()

workspace_memory = Table(
    "workspace_memory",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user", Text),
    Column("session_id", Text),
    Column("topic", Text, nullable=False, default=""),
    Column("context", Text, nullable=False, default=""),
    Column("key_points", Text, nullable=False, default="[]"),
    Column("tags", Text, nullable=False, default="[]"),
    Column("related_sessions", Text, nullable=False, default="[]"),
    Column("created_at", Text, nullable=False),
    Column("source_agent", Text),
)
