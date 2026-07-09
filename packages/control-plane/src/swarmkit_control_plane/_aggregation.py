"""AggregationStore — append-only, deduped central store for audit/eval/usage.

Instances push these three SwarmKit-specific signals to the panel (design
details/control-plane/14-aggregation.md). Raw traces/metrics stay in a BYO OTel collector and are
not handled here; live jobs are federated (queried on demand), not stored.

Append-only + deduped by ``(instance_id, kind, record_id)`` so at-least-once pushes are idempotent
(the design's `event_id` / `(instance, eval_set, ts)` keys collapse into the caller-supplied
``record_id``). SQLAlchemy Core over SQLite (default) or Postgres
(design/details/postgres-backend.md). The ``payload`` is a JSON column so the rollups extract fields
from it in SQL — SQLAlchemy renders ``JSON_EXTRACT`` on SQLite and the ``->>`` operators on
Postgres — so the signal shape can evolve without a migration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from swarmkit_control_plane._store_base import Store, upsert
from swarmkit_control_plane._tables import agg_records

KINDS = ("audit", "eval", "usage", "gap")


class AggregationStore(Store):
    """Thread-safe store for pushed audit/eval/usage records."""

    def ingest(self, instance_id: str, kind: str, records: list[dict[str, Any]]) -> dict[str, int]:
        """Append records for an instance. Returns counts of ingested vs deduped (and skipped).

        Each record needs a stable id (``id`` or ``event_id``); records without one are skipped.
        """
        ingested = deduped = skipped = 0
        with self._lock, self._engine.begin() as conn:
            for rec in records:
                rid = rec.get("id") or rec.get("event_id")
                if not rid:
                    skipped += 1
                    continue
                # RETURNING tells insert (a row comes back) from conflict (none) portably —
                # psycopg reports rowcount=-1 for ON CONFLICT, so rowcount can't be trusted here.
                stmt = upsert(
                    self._engine,
                    agg_records,
                    {
                        "instance_id": instance_id,
                        "kind": kind,
                        "record_id": str(rid),
                        "ts": str(rec.get("ts", "")),
                        "payload": rec,
                    },
                    index_elements=["instance_id", "kind", "record_id"],
                ).returning(agg_records.c.record_id)
                if conn.execute(stmt).first() is not None:
                    ingested += 1
                else:
                    deduped += 1
        return {"ingested": ingested, "deduped": deduped, "skipped": skipped}

    def put_usage_snapshot(
        self, instance_id: str, by_model: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Replace an instance's *pulled* usage rollup (design 23 — usage pull-on-sync).

        Unlike :meth:`ingest` (append-only event records, dedup-and-forget), a pulled ``/usage``
        rollup is a **cumulative snapshot**: re-syncing must *refresh* the totals, not dedup them
        away. So each model row is upserted under a reserved ``pull:<model>`` record-id with
        ``DO UPDATE`` (replace-on-sync). The ``pull:`` prefix keeps pulled snapshots distinct
        from pushed event ids, and :meth:`usage_rollup` (group by model+provider) folds them in.

        ``by_model`` rows carry serve's shape (``model, calls, input_tokens, output_tokens,
        cost_usd``); ``provider`` is absent from serve and stored as ``null``. Returns the number of
        model rows written.
        """
        written = 0
        with self._lock, self._engine.begin() as conn:
            for row in by_model:
                model = row.get("model")
                if not model:
                    continue
                payload = {
                    "model": model,
                    "provider": row.get("provider"),
                    "input_tokens": row.get("input_tokens", 0),
                    "output_tokens": row.get("output_tokens", 0),
                    "cost_usd": row.get("cost_usd", 0),
                    "calls": row.get("calls", 0),
                    "source": "pull",
                }
                record = {
                    "instance_id": instance_id,
                    "kind": "usage",
                    "record_id": f"pull:{model}",
                    "ts": str(row.get("ts", "")),
                    "payload": payload,
                }
                conn.execute(
                    upsert(
                        self._engine,
                        agg_records,
                        record,
                        index_elements=["instance_id", "kind", "record_id"],
                        set_={"ts": record["ts"], "payload": payload},
                    )
                )
                written += 1
        return {"written": written}

    def usage_rollup(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        """Token/cost totals grouped by model + provider. Fleet-wide by default; pass
        *instance_id* to scope to one instance (design 24 — instance-scoped observability)."""
        payload = agg_records.c.payload
        model = payload["model"].as_string()
        provider = payload["provider"].as_string()
        stmt = (
            select(
                model.label("model"),
                provider.label("provider"),
                func.coalesce(func.sum(payload["input_tokens"].as_float()), 0).label(
                    "input_tokens"
                ),
                func.coalesce(func.sum(payload["output_tokens"].as_float()), 0).label(
                    "output_tokens"
                ),
                func.coalesce(func.sum(payload["cost_usd"].as_float()), 0).label("cost_usd"),
                func.count().label("records"),
            )
            .where(agg_records.c.kind == "usage")
            .group_by(model, provider)
            .order_by(func.count().desc())
        )
        if instance_id is not None:
            stmt = stmt.where(agg_records.c.instance_id == instance_id)
        with self._lock, self._engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]

    def eval_summary(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        """Pass-rate per (eval_set, topology). Fleet-wide by default; pass *instance_id* to scope
        to one instance (design 24)."""
        payload = agg_records.c.payload
        eval_set = payload["eval_set"].as_string()
        topology = payload["topology"].as_string()
        stmt = (
            select(
                eval_set.label("eval_set"),
                topology.label("topology"),
                func.coalesce(func.sum(payload["passed"].as_float()), 0).label("passed"),
                func.coalesce(func.sum(payload["total"].as_float()), 0).label("total"),
                func.count().label("runs"),
            )
            .where(agg_records.c.kind == "eval")
            .group_by(eval_set, topology)
            .order_by(eval_set)
        )
        if instance_id is not None:
            stmt = stmt.where(agg_records.c.instance_id == instance_id)
        with self._lock, self._engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
        for row in rows:
            total = row["total"] or 0
            row["pass_rate"] = round(row["passed"] / total, 4) if total else None
        return rows

    def recent_audit(
        self, limit: int = 100, instance_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Most-recent audit events, each tagged with its instance. Fleet-wide by default; pass
        *instance_id* to scope to one instance (design 24)."""
        stmt = (
            select(agg_records.c.instance_id, agg_records.c.payload)
            .where(agg_records.c.kind == "audit")
            .order_by(agg_records.c.ts.desc())
            .limit(limit)
        )
        if instance_id is not None:
            stmt = stmt.where(agg_records.c.instance_id == instance_id)
        with self._lock, self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [{"instance_id": r["instance_id"], **(r["payload"] or {})} for r in rows]

    def gap_rollup(self) -> list[dict[str, Any]]:
        """Skill gaps ranked across the fleet (signal → surface, design 17). Instances push
        a gap signal (``{id, capability, description, ts}``) whenever a worker wants a
        capability it lacks; this ranks them by how often they recur and how many distinct
        instances hit them — the panel surfaces the top gaps for a human to turn into a
        proposal. A frequent, fleet-wide gap ranks above a one-off."""
        payload = agg_records.c.payload
        capability = payload["capability"].as_string()
        stmt = (
            select(
                capability.label("capability"),
                func.count().label("occurrences"),
                func.count(func.distinct(agg_records.c.instance_id)).label("instances"),
                func.max(agg_records.c.ts).label("last_seen"),
                func.max(payload["description"].as_string()).label("description"),
            )
            .where(agg_records.c.kind == "gap")
            .group_by(capability)
            .order_by(
                func.count().desc(), func.count(func.distinct(agg_records.c.instance_id)).desc()
            )
        )
        with self._lock, self._engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]
