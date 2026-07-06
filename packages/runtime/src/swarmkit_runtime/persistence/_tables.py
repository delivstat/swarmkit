"""SQLAlchemy Core table definitions for the runtime persistence store.

One ``MetaData`` / ``Table`` set drives both SQLite and Postgres (see postgres-backend.md).
Columns match the original hand-written schema exactly so existing SQLite databases keep working
under ``metadata.create_all`` (which only creates what's absent). Timestamps + JSON blobs are stored
as ``Text`` (ISO strings / ``json.dumps``) — dialect-agnostic and identical to the prior behaviour.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text

metadata = MetaData()

jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("topology", Text, nullable=False),
    Column("status", Text, nullable=False, default="pending"),
    Column("input", Text, nullable=False),
    Column("version", Text),
    Column("output", Text),
    Column("error", Text),
    Column("events", Text, default="[]"),
    Column("created_at", Text, nullable=False),
    Column("completed_at", Text),
    Column("usage_input_tokens", Integer, default=0),
    Column("usage_output_tokens", Integer, default=0),
    Column("usage_cost_usd", Float, default=0.0),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Text, primary_key=True),
    Column("topology", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("turns", Text, default="[]"),
    Column("metadata", Text, default="{}"),
)

run_usage = Table(
    "run_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Text),
    Column("conversation_id", Text),
    Column("agent_id", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, default=0),
    Column("output_tokens", Integer, default=0),
    Column("cache_read_tokens", Integer, default=0),
    Column("cost_usd", Float, default=0.0),
    Column("created_at", Text, nullable=False),
)

serve_access = Table(
    "serve_access",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("action", Text),
    Column("status", Integer),
    Column("created_at", Text, nullable=False),
)
