"""Phase 8 hardening: the sqlite stores run in WAL mode so connector pushes and
operator reads don't block each other under a real fleet's concurrency."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text
from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry


def test_stores_use_wal_journal(tmp_path: Path) -> None:
    stores: list[Any] = [
        SqliteRegistry(tmp_path / "reg.sqlite"),
        AggregationStore(tmp_path / "agg.sqlite"),
        ArtifactStore(tmp_path / "art.sqlite"),
        ProposalStore(tmp_path / "prop.sqlite"),
    ]
    for store in stores:
        with store.engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert str(mode).lower() == "wal", f"{type(store).__name__} journal_mode={mode}"
        assert busy == 10000, f"{type(store).__name__} busy_timeout={busy}"
