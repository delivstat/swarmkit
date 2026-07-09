"""Tests for the aggregation store + push API (audit/eval/usage) and its auth scoping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import AggregationStore, SqliteRegistry, create_app

_OP = "operator-secret"


def _store(tmp_path: Path) -> AggregationStore:
    return AggregationStore(tmp_path / "agg.sqlite")


# --- store ----------------------------------------------------------------------


def test_usage_ingest_is_append_only_and_deduped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recs = [
        {
            "id": "u1",
            "model": "gpt-4o",
            "provider": "openai",
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.5,
        },
        {
            "id": "u2",
            "model": "gpt-4o",
            "provider": "openai",
            "input_tokens": 50,
            "output_tokens": 10,
            "cost_usd": 0.25,
        },
    ]
    first = store.ingest("i1", "usage", recs)
    assert first == {"ingested": 2, "deduped": 0, "skipped": 0}
    # Re-push (at-least-once) → deduped, totals unchanged.
    again = store.ingest("i1", "usage", recs)
    assert again["ingested"] == 0 and again["deduped"] == 2

    rollup = store.usage_rollup()
    assert len(rollup) == 1
    row = rollup[0]
    assert row["model"] == "gpt-4o" and row["provider"] == "openai"
    assert row["input_tokens"] == 150 and row["output_tokens"] == 30
    assert row["records"] == 2


def test_put_usage_snapshot_replaces_on_resync(tmp_path: Path) -> None:
    # A pulled /usage rollup is a cumulative snapshot (design 23): re-syncing must *refresh* the
    # totals, not dedup them away (as ingest would) nor sum them (as append would).
    store = _store(tmp_path)
    first = store.put_usage_snapshot(
        "i1", [{"model": "kimi-k2", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.5}]
    )
    assert first == {"written": 1}
    r1 = store.usage_rollup()
    assert len(r1) == 1
    assert r1[0]["model"] == "kimi-k2" and r1[0]["provider"] is None  # serve omits provider
    assert r1[0]["input_tokens"] == 100 and r1[0]["records"] == 1

    # Re-sync with grown cumulative totals → the row is REPLACED, not added.
    store.put_usage_snapshot(
        "i1", [{"model": "kimi-k2", "input_tokens": 250, "output_tokens": 60, "cost_usd": 1.2}]
    )
    r2 = store.usage_rollup()
    assert len(r2) == 1  # still one row — replaced, not duplicated
    assert r2[0]["input_tokens"] == 250 and r2[0]["output_tokens"] == 60
    assert r2[0]["records"] == 1


def test_put_usage_snapshot_skips_rows_without_model(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = store.put_usage_snapshot("i1", [{"input_tokens": 5}, {"model": "m", "input_tokens": 3}])
    assert out == {"written": 1}
    assert store.usage_rollup()[0]["model"] == "m"


def test_pulled_snapshot_and_pushed_events_are_separate_records(tmp_path: Path) -> None:
    # The reserved pull:<model> record-id keeps a pulled snapshot distinct from pushed event ids,
    # so a pull never collides with (nor is deduped against) a pushed usage event.
    store = _store(tmp_path)
    store.ingest("i1", "usage", [{"id": "evt-1", "model": "m", "input_tokens": 10}])
    store.put_usage_snapshot("i1", [{"model": "m", "input_tokens": 40}])
    rollup = store.usage_rollup()
    assert len(rollup) == 1 and rollup[0]["model"] == "m"
    assert rollup[0]["records"] == 2  # one pushed event + one pulled snapshot, both retained


def test_records_without_id_are_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = store.ingest("i1", "usage", [{"model": "x"}])
    assert out["skipped"] == 1 and out["ingested"] == 0


def test_same_id_different_instances_not_deduped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = [{"id": "u1", "model": "m", "provider": "p", "input_tokens": 1, "output_tokens": 1}]
    store.ingest("i1", "usage", rec)
    store.ingest("i2", "usage", rec)
    assert store.usage_rollup()[0]["records"] == 2  # deduped per (instance, kind, id)


def test_eval_summary_pass_rate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ingest(
        "i1",
        "eval",
        [
            {"id": "e1", "eval_set": "smoke", "topology": "hello", "passed": 8, "total": 10},
            {"id": "e2", "eval_set": "smoke", "topology": "hello", "passed": 9, "total": 10},
        ],
    )
    summary = store.eval_summary()
    assert len(summary) == 1
    assert summary[0]["passed"] == 17 and summary[0]["total"] == 20
    assert summary[0]["pass_rate"] == 0.85


def test_recent_audit_tags_instance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ingest("dc-1", "audit", [{"id": "a1", "ts": "2026-01-01", "action": "run"}])
    rows = store.recent_audit()
    assert rows[0]["instance_id"] == "dc-1" and rows[0]["action"] == "run"


def test_rollups_scope_to_one_instance(tmp_path: Path) -> None:
    # design 24: fleet-wide by default, ?instance_id scopes to one instance.
    store = _store(tmp_path)
    store.ingest(
        "i1", "usage", [{"id": "u1", "model": "m", "input_tokens": 100, "output_tokens": 5}]
    )
    store.ingest(
        "i2", "usage", [{"id": "u2", "model": "m", "input_tokens": 900, "output_tokens": 5}]
    )
    store.ingest(
        "i1", "eval", [{"id": "e1", "eval_set": "s", "topology": "t", "passed": 2, "total": 2}]
    )
    store.ingest(
        "i2", "eval", [{"id": "e2", "eval_set": "s", "topology": "t", "passed": 0, "total": 2}]
    )
    store.ingest("i1", "audit", [{"id": "a1", "ts": "2026-01-02", "action": "run"}])
    store.ingest("i2", "audit", [{"id": "a2", "ts": "2026-01-03", "action": "run"}])

    # fleet-wide sums both instances
    assert store.usage_rollup()[0]["input_tokens"] == 1000
    # scoped to i1 only
    scoped = store.usage_rollup("i1")
    assert len(scoped) == 1 and scoped[0]["input_tokens"] == 100

    assert store.eval_summary("i1")[0]["pass_rate"] == 1.0  # i1 passed 2/2
    assert store.eval_summary("i2")[0]["pass_rate"] == 0.0  # i2 passed 0/2

    audit_i2 = store.recent_audit(instance_id="i2")
    assert len(audit_i2) == 1 and audit_i2[0]["instance_id"] == "i2"


# --- endpoints (open mode) ------------------------------------------------------


def _client(tmp_path: Path, *, enforce: bool = False) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    store = AggregationStore(tmp_path / "registry.sqlite")  # share the db

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(
        create_app(
            registry,
            verify=verify,
            aggregation=store,
            operator_tokens=[_OP] if enforce else None,
        )
    )


def test_audit_limit_is_bounded(tmp_path: Path) -> None:
    # sqlite treats a negative LIMIT as unbounded (whole-table dump); the endpoint must
    # reject out-of-range limits rather than pass them through.
    client = _client(tmp_path)
    assert client.get("/audit?limit=-1").status_code == 422
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?limit=100000").status_code == 422
    assert client.get("/audit?limit=50").status_code == 200


def test_push_and_rollup_via_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/aggregate/usage",
        json={
            "instance_id": "i1",
            "records": [
                {
                    "id": "u1",
                    "model": "m",
                    "provider": "p",
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "cost_usd": 0.1,
                }
            ],
        },
    )
    assert resp.status_code == 200 and resp.json()["ingested"] == 1
    assert client.get("/usage").json()[0]["input_tokens"] == 5


def test_usage_endpoint_scopes_by_instance_id(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for iid, tok in (("i1", 5), ("i2", 50)):
        client.post(
            "/aggregate/usage",
            json={
                "instance_id": iid,
                "records": [{"id": f"u-{iid}", "model": "m", "input_tokens": tok}],
            },
        )
    # fleet-wide sums both; ?instance_id narrows to one.
    assert client.get("/usage").json()[0]["input_tokens"] == 55
    scoped = client.get("/usage?instance_id=i2").json()
    assert len(scoped) == 1 and scoped[0]["input_tokens"] == 50
    assert client.get("/audit?instance_id=i1&limit=10").json() == []  # no i1 audit rows


def test_unknown_kind_404_and_missing_instance_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/aggregate/bogus", json={"records": []}).status_code == 404
    # open mode: no principal, no instance_id → 400
    assert client.post("/aggregate/usage", json={"records": [{"id": "x"}]}).status_code == 400


def test_connector_pushes_as_itself_operator_reads(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    op = {"Authorization": f"Bearer {_OP}"}
    iid = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "run"},
        headers=op,
    ).json()["id"]
    token = client.post(f"/instances/{iid}/mint-token", json={}, headers=op).json()["token"]
    conn = {"Authorization": f"Bearer {token}"}

    # Connector pushes without naming an instance — the panel scopes it to the principal's id.
    pushed = client.post(
        "/aggregate/usage",
        json={
            "records": [
                {"id": "u1", "model": "m", "provider": "p", "input_tokens": 3, "output_tokens": 1}
            ]
        },
        headers=conn,
    )
    assert pushed.status_code == 200 and pushed.json()["ingested"] == 1

    # Operator reads the rollup; the connector may not read the fleet view.
    assert client.get("/usage", headers=op).json()[0]["records"] == 1
    assert client.get("/usage", headers=conn).status_code == 403
