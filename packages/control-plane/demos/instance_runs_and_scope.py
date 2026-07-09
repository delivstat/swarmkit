"""Demo: federated per-run detail + instance-scoped aggregates (design 24).

The two-lane observability model:
  * Aggregates (cost/usage rollups) are PUSHED and stored — scopeable per instance.
  * Per-run details are FEDERATED live from the instance and NEVER stored — with a reachability
    envelope so the UI can show "instance unavailable" honestly.

Everything but the instances is the real panel: real registry, real AggregationStore, the real
/instances/{id}/runs route + the real scoped rollups. The instances' serve is stubbed (one direct
Mode-A with run history, one poll Mode-B) so the demo needs no live serve.

Run it:

    uv run python packages/control-plane/demos/instance_runs_and_scope.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import AggregationStore, SqliteRegistry, create_app
from swarmkit_control_plane._models import Instance

# Instance "alpha" (Mode A) has run history with per-run cost; "bravo" (Mode B) can't be federated.
_RUNS = [
    {
        "job_id": "j1",
        "topology": "solution-design",
        "status": "completed",
        "usage_input_tokens": 1800,
        "usage_output_tokens": 320,
        "usage_cost_usd": 0.042,
    },
    {
        "job_id": "j2",
        "topology": "code-review",
        "status": "completed",
        "usage_input_tokens": 640,
        "usage_output_tokens": 90,
        "usage_cost_usd": 0.011,
    },
]


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "runs_demo.sqlite"
    registry = SqliteRegistry(db)
    registry.add(
        Instance(
            id="alpha01",
            name="alpha",
            endpoint="http://alpha.local:8001",
            connection="direct",
            health="healthy",
            created_at="2026-07-09T09:00:00Z",
        )
    )
    registry.add(
        Instance(
            id="bravo02",
            name="bravo",
            endpoint="http://bravo.local:8002",
            connection="poll",
            health="healthy",
            created_at="2026-07-09T09:00:00Z",
        )
    )

    # Pushed aggregates (the "always available" lane) — one row per instance.
    agg = AggregationStore(db)
    agg.ingest(
        "alpha01",
        "usage",
        [
            {
                "id": "a1",
                "model": "kimi-k2",
                "input_tokens": 2440,
                "output_tokens": 410,
                "cost_usd": 0.053,
            }
        ],
    )
    agg.ingest(
        "bravo02",
        "usage",
        [
            {
                "id": "b1",
                "model": "deepseek-v3",
                "input_tokens": 9000,
                "output_tokens": 1200,
                "cost_usd": 0.180,
            }
        ],
    )

    async def fetch_runs(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
        return _RUNS  # only alpha (direct) ever reaches here

    client = TestClient(create_app(registry, aggregation=agg, runs=fetch_runs))

    _bar("Aggregate lane (PUSHED, stored) — fleet-wide, then scoped per instance")
    print(
        "  GET /usage                 →",
        [(r["model"], r["cost_usd"]) for r in client.get("/usage").json()],
    )
    print(
        "  GET /usage?instance_id=alpha01 →",
        [(r["model"], r["cost_usd"]) for r in client.get("/usage?instance_id=alpha01").json()],
    )

    _bar("Detail lane (FEDERATED, not stored) — alpha is a reachable Mode-A instance")
    body = client.get("/instances/alpha01/runs").json()
    print(f"  reachable={body['reachable']} reason={body['reason']}")
    for r in body["runs"]:
        cost = r["usage_cost_usd"]
        print(f"    {r['job_id']}  {r['topology']:<16} {r['status']:<10} cost=${cost:.3f}")

    _bar("Detail lane — bravo is a Mode-B (poll) instance: aggregate shows, live detail can't")
    body = client.get("/instances/bravo02/runs").json()
    print(
        f"  GET /instances/bravo02/runs → reachable={body['reachable']} "
        f"reason={body['reason']} (UI shows 'unavailable')"
    )
    print(
        f"  …but its pushed cost is still visible: "
        f"{[(r['model'], r['cost_usd']) for r in client.get('/usage?instance_id=bravo02').json()]}"
    )

    print(
        "\nAggregates survive offline + scope per instance; per-run detail is live + never stored."
    )


if __name__ == "__main__":
    main()
