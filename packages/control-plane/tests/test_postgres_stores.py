"""The four panel stores on a real Postgres — runs only when SWARMKIT_TEST_POSTGRES_URL is set.

Deselected by default (``integration`` marker), like the runtime's Postgres store/audit tests: CI
stays SQLite-only and the existing suites guard behaviour; a developer/CD points this at a real
Postgres to verify the dialect end-to-end (upserts, ``FOR UPDATE SKIP LOCKED``, JSON rollups). The
stores share one engine, mirroring how ``create_app`` wires them.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import Engine
from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._engine import make_engine
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry


def _pg_engine() -> Engine:
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres control-plane store tests")
    return make_engine(url)


@pytest.mark.integration
def test_registry_and_command_queue_on_postgres() -> None:
    engine = _pg_engine()
    reg = SqliteRegistry(engine)
    assert reg.engine.dialect.name == "postgresql"
    iid = f"i-{uuid.uuid4().hex[:8]}"
    reg.add(Instance(id=iid, name="edge", endpoint="n/a", connection="poll", tier="run"))
    assert reg.get(iid) is not None
    # re-add (INSERT OR REPLACE) refreshes fields, not a duplicate.
    reg.add(Instance(id=iid, name="edge2", endpoint="n/a", connection="poll", tier="run"))
    got = reg.get(iid)
    assert got is not None and got.name == "edge2"

    for i in range(5):
        reg.enqueue(iid, "capabilities", {"i": i})
    claimed = reg.claim_queued(iid, limit=10)
    assert len(claimed) == 5 and all(c.status == "dispatched" for c in claimed)
    assert reg.claim_queued(iid) == []  # drained → no double-dispatch
    assert reg.record_result(claimed[0].cmd_id, status="done", output={"ok": True}) is True
    assert reg.record_result(claimed[0].cmd_id, status="done") is False  # idempotent


@pytest.mark.integration
def test_artifacts_and_proposals_on_postgres() -> None:
    engine = _pg_engine()
    arts = ArtifactStore(engine)
    props = ProposalStore(engine)
    aid = f"topo-{uuid.uuid4().hex[:8]}"
    v1 = arts.register_version("topology", aid, content={"nodes": ["root"]})
    assert v1["version"] == "v1"
    # identical content is idempotent; changed content is a new version.
    assert arts.register_version("topology", aid, content={"nodes": ["root"]})["version"] == "v1"
    assert arts.register_version("topology", aid, content={"nodes": ["a"]})["version"] == "v2"

    p = props.create(kind="skill", artifact_id=aid, content={"category": "capability"})
    assert p["status"] == "pending"
    approved = props.mark_approved(p["id"], approved_by="alice", published_version="v1")
    assert approved["status"] == "approved"
    with pytest.raises(ValueError, match="not pending"):
        props.mark_approved(p["id"], approved_by="bob", published_version="v2")


@pytest.mark.integration
def test_aggregation_rollups_on_postgres() -> None:
    engine = _pg_engine()
    agg = AggregationStore(engine)
    inst = f"i-{uuid.uuid4().hex[:8]}"
    # usage_rollup groups across all instances, and the Postgres test DB persists between runs, so
    # scope the assertion with a model/provider unique to this run.
    model = f"m-{uuid.uuid4().hex[:8]}"
    provider = f"p-{uuid.uuid4().hex[:8]}"
    recs = [
        {"id": "u1", "model": model, "provider": provider, "input_tokens": 100, "cost_usd": 0.5},
        {"id": "u2", "model": model, "provider": provider, "input_tokens": 50, "cost_usd": 0.25},
    ]
    assert agg.ingest(inst, "usage", recs)["ingested"] == 2
    assert agg.ingest(inst, "usage", recs)["deduped"] == 2  # at-least-once dedup
    rollup = [r for r in agg.usage_rollup() if r["provider"] == provider]
    assert len(rollup) == 1 and rollup[0]["input_tokens"] == 150 and rollup[0]["records"] == 2
