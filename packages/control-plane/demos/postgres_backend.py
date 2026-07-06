"""Demo: the same control-plane stores on SQLite and (optionally) Postgres.

Proves the PR-3 claim — one SQLAlchemy-Core implementation per store drives both dialects
(design/details/postgres-backend.md). It enrols an instance, queues + claims commands (the
cross-process-atomic ``claim_queued``), registers an artifact version, opens/approves a proposal,
and ingests + rolls up usage — first on a throwaway SQLite file, then, if ``DATABASE_URL`` (or
``SWARMKIT_CONTROL_PLANE_STORE_URL``) points at a Postgres, the identical calls on Postgres.

Run it:

    uv run python packages/control-plane/demos/postgres_backend.py                 # sqlite only
    DATABASE_URL=postgresql://postgres:pw@127.0.0.1:5432/swarmkit \\
        uv run python packages/control-plane/demos/postgres_backend.py             # + postgres
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._engine import make_engine, sqlite_url
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry


def exercise(label: str, url: str) -> None:
    engine = make_engine(url)
    reg = SqliteRegistry(engine)
    arts = ArtifactStore(engine)  # share the one engine, like create_app does
    props = ProposalStore(engine)
    agg = AggregationStore(engine)
    print(f"\n=== {label} (dialect: {engine.dialect.name}) ===")

    iid = f"edge-{uuid.uuid4().hex[:6]}"
    reg.add(Instance(id=iid, name="edge", endpoint="n/a", connection="poll", tier="run"))
    for i in range(3):
        reg.enqueue(iid, "capabilities", {"i": i})
    claimed = reg.claim_queued(iid)
    print(f"enrolled {iid}; claimed {len(claimed)} commands, re-claim -> {reg.claim_queued(iid)}")

    aid = f"topo-{uuid.uuid4().hex[:6]}"
    v1 = arts.register_version("topology", aid, content={"nodes": ["root"]})
    same = arts.register_version("topology", aid, content={"nodes": ["root"]})
    print(f"artifact {aid} -> {v1['version']}; idempotent re-register -> {same['version']}")

    p = props.create(kind="skill", artifact_id=aid, content={"category": "capability"})
    approved = props.mark_approved(p["id"], approved_by="alice", published_version="v1")
    print(f"proposal {p['id']} {p['status']} -> {approved['status']}")

    model = f"m-{uuid.uuid4().hex[:6]}"
    recs = [{"id": "u1", "model": model, "provider": "demo", "input_tokens": 100, "cost_usd": 0.5}]
    first = agg.ingest(iid, "usage", recs)
    dup = agg.ingest(iid, "usage", recs)
    rollup = next(r for r in agg.usage_rollup() if r["model"] == model)
    print(f"usage ingest {first}, re-ingest {dup}; rollup input_tokens={rollup['input_tokens']}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exercise("SQLite", sqlite_url(Path(tmp) / "demo.sqlite"))

    pg = os.environ.get("SWARMKIT_CONTROL_PLANE_STORE_URL") or os.environ.get("DATABASE_URL")
    if pg:
        exercise("Postgres", pg)
    else:
        print("\n(set DATABASE_URL to a Postgres to run the identical flow there)")


if __name__ == "__main__":
    main()
