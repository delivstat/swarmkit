"""AggregationStore — append-only, deduped central store for audit/eval/usage.

Instances push these three SwarmKit-specific signals to the panel (design
details/control-plane/14-aggregation.md). Raw traces/metrics stay in a BYO OTel collector and are
not handled here; live jobs are federated (queried on demand), not stored.

Append-only + deduped by ``(instance_id, kind, record_id)`` so at-least-once pushes are idempotent
(the design's `event_id` / `(instance, eval_set, ts)` keys collapse into the caller-supplied
``record_id``). Sqlite for now, mirroring the registry; the design's central Postgres is a later
swap. Rollups use ``json_extract`` over the stored payload, so the signal shape can evolve without a
migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

KINDS = ("audit", "eval", "usage", "gap")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agg_records (
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (instance_id, kind, record_id)
);
CREATE INDEX IF NOT EXISTS idx_agg_kind_ts ON agg_records (kind, ts);
"""


class AggregationStore:
    """Thread-safe sqlite store for pushed audit/eval/usage records."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL lets connector pushes and operator reads proceed concurrently without
        # blocking; busy_timeout retries under contention instead of raising "locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def ingest(self, instance_id: str, kind: str, records: list[dict[str, Any]]) -> dict[str, int]:
        """Append records for an instance. Returns counts of ingested vs deduped (and skipped).

        Each record needs a stable id (``id`` or ``event_id``); records without one are skipped.
        """
        ingested = deduped = skipped = 0
        with self._lock, self._connect() as conn:
            for rec in records:
                rid = rec.get("id") or rec.get("event_id")
                if not rid:
                    skipped += 1
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO agg_records (instance_id, kind, record_id, ts, payload)
                       VALUES (?, ?, ?, ?, ?)""",
                    (instance_id, kind, str(rid), str(rec.get("ts", "")), json.dumps(rec)),
                )
                if cur.rowcount:
                    ingested += 1
                else:
                    deduped += 1
        return {"ingested": ingested, "deduped": deduped, "skipped": skipped}

    def usage_rollup(self) -> list[dict[str, Any]]:
        """Token/cost totals grouped by model + provider across the fleet."""
        sql = """
            SELECT json_extract(payload, '$.model') AS model,
                   json_extract(payload, '$.provider') AS provider,
                   COALESCE(SUM(json_extract(payload, '$.input_tokens')), 0) AS input_tokens,
                   COALESCE(SUM(json_extract(payload, '$.output_tokens')), 0) AS output_tokens,
                   COALESCE(SUM(json_extract(payload, '$.cost_usd')), 0) AS cost_usd,
                   COUNT(*) AS records
            FROM agg_records WHERE kind = 'usage'
            GROUP BY model, provider ORDER BY records DESC
        """
        with self._lock, self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def eval_summary(self) -> list[dict[str, Any]]:
        """Pass-rate per (eval_set, topology) across the fleet."""
        sql = """
            SELECT json_extract(payload, '$.eval_set') AS eval_set,
                   json_extract(payload, '$.topology') AS topology,
                   COALESCE(SUM(json_extract(payload, '$.passed')), 0) AS passed,
                   COALESCE(SUM(json_extract(payload, '$.total')), 0) AS total,
                   COUNT(*) AS runs
            FROM agg_records WHERE kind = 'eval'
            GROUP BY eval_set, topology ORDER BY eval_set
        """
        with self._lock, self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
        for row in rows:
            total = row["total"] or 0
            row["pass_rate"] = round(row["passed"] / total, 4) if total else None
        return rows

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        """Most-recent audit events across the fleet, each tagged with its instance."""
        sql = """
            SELECT instance_id, payload FROM agg_records WHERE kind = 'audit'
            ORDER BY ts DESC LIMIT ?
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [{"instance_id": r["instance_id"], **json.loads(r["payload"])} for r in rows]

    def gap_rollup(self) -> list[dict[str, Any]]:
        """Skill gaps ranked across the fleet (signal → surface, design 17). Instances push
        a gap signal (``{id, capability, description, ts}``) whenever a worker wants a
        capability it lacks; this ranks them by how often they recur and how many distinct
        instances hit them — the panel surfaces the top gaps for a human to turn into a
        proposal. A frequent, fleet-wide gap ranks above a one-off."""
        sql = """
            SELECT json_extract(payload, '$.capability') AS capability,
                   COUNT(*) AS occurrences,
                   COUNT(DISTINCT instance_id) AS instances,
                   MAX(ts) AS last_seen,
                   MAX(json_extract(payload, '$.description')) AS description
            FROM agg_records WHERE kind = 'gap'
            GROUP BY capability
            ORDER BY occurrences DESC, instances DESC
        """
        with self._lock, self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]
