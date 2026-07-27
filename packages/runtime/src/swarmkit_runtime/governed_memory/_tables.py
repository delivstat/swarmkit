"""SQLAlchemy Core tables for governed memory (design/details/governed-memory.md).

Two tables, one invariant (design §8.3): ``governed_memory`` is the **mutable canonical
current-state** — one row per ``(subject, attribute)`` key, upserted as a fact evolves — and
``governed_memory_change_log`` is the **append-only** record of every mutation. The memory row
changes; the record of change never does. Together they give update-in-place *and* a full history
you can read a fact ``as_of`` any past time — the "git for memory" idea as current-state-plus-audit,
on the SQLite/Postgres backend the runtime already runs (no bespoke store).

Its own ``MetaData`` (separate from the core persistence tables) so it can create-all independently;
it shares the same engine/dialect handling via ``persistence._store.make_engine``. Timestamps + JSON
ride as ``Text`` (ISO strings / ``json.dumps``) — dialect-agnostic, matching the persistence store.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text

metadata = MetaData()

#: The canonical current-state of memory — one row per ``(subject, attribute)`` key.
memory = Table(
    "governed_memory",
    metadata,
    Column("key", Text, primary_key=True),  # f"{subject}::{attribute}" — the reconciliation anchor
    Column("subject", Text, nullable=False),
    Column("attribute", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("type", Text, nullable=False, default="semantic"),
    Column("confidence", Float, nullable=False, default=1.0),
    Column("content_hash", Text, nullable=False),  # sha256(value) — drives exact-dedup → reinforce
    Column("valid_from", Text, nullable=False),  # when this key's memory first appeared
    Column("last_reinforced_at", Text, nullable=False),  # recency; bumped on every op
    Column("reinforce_count", Integer, nullable=False, default=1),
    Column("source", Text),
    Column("provenance", Text, nullable=False, default="{}"),
    Column("status", Text, nullable=False, default="active"),  # active | quarantined (judge slice)
)

#: Append-only — every mutation of a memory. Never updated or deleted (§8.3).
change_log = Table(
    "governed_memory_change_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("memory_key", Text, nullable=False),
    Column("op", Text, nullable=False),  # new | update | reinforce (refine/quarantine: judge slice)
    Column("before", Text),  # json snapshot of the row before the op, or null on `new`
    Column("after", Text, nullable=False),  # json snapshot of the row after the op
    Column("reason", Text, nullable=False, default=""),
    Column("decided_by", Text, nullable=False, default="deterministic"),
    Column("timestamp", Text, nullable=False),
)
