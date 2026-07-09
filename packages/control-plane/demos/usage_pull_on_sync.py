"""Demo: usage pull-on-sync (design 23).

The fleet Runs page reads the usage rollup. Today it's populated only when instances
*push* usage events. This demo shows the new pull path: ``POST /instances/{id}/sync``
now also pulls the instance's ``/usage`` rollup (Mode A) and folds it into the panel
aggregation — so a directly-reachable instance that has run workloads shows real
per-model totals on the Runs page without any push wiring.

Everything but the instance is the real panel code path: a real registry, a real
AggregationStore, the real ``/sync`` route, and the real ``usage_rollup()`` behind
``GET /usage``. Only the instance's serve is stubbed (a Mode-A instance that has
recorded usage), so the demo needs no live serve and burns no API budget. Re-syncing
shows the rollup *refresh* (cumulative snapshot), not double-count.

Run it:

    uv run python packages/control-plane/demos/usage_pull_on_sync.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ManifestUnsupported
from swarmkit_control_plane._models import Instance

# A Mode-A instance's observed state (minimal — this demo is about usage, not inventory).
_STATE = {
    "apiVersion": "swarmkit/v1",
    "kind": "InstanceState",
    "workspace_id": "sterling-oms",
    "schema_version": "1.9.0",
    "artifacts": {"topologies": [], "skills": [], "archetypes": [], "triggers": []},
    "providers": ["moonshot", "deepseek"],
    "governance_provider": "mock",
    "health": {"status": "ok"},
}

# The instance's cumulative /usage rollup — it grows as work runs between syncs.
_USAGE_T1 = {
    "by_model": [
        {
            "model": "kimi-k2",
            "calls": 9,
            "input_tokens": 18_000,
            "output_tokens": 3_200,
            "cost_usd": 0.42,
        },
        {
            "model": "deepseek-v3",
            "calls": 3,
            "input_tokens": 6_500,
            "output_tokens": 900,
            "cost_usd": 0.08,
        },
    ],
}
_USAGE_T2 = {
    "by_model": [
        {
            "model": "kimi-k2",
            "calls": 15,
            "input_tokens": 31_000,
            "output_tokens": 5_400,
            "cost_usd": 0.71,
        },
        {
            "model": "deepseek-v3",
            "calls": 5,
            "input_tokens": 9_800,
            "output_tokens": 1_500,
            "cost_usd": 0.13,
        },
    ],
}


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def _show_rollup(client: TestClient) -> None:
    for row in client.get("/usage").json():
        print(
            f"    {row['model']:<14} in={row['input_tokens']:>6}  "
            f"out={row['output_tokens']:>5}  cost=${row['cost_usd']:.2f}  "
            f"({row['records']} snapshot)"
        )


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "usage_pull.sqlite"
    registry = SqliteRegistry(db)
    registry.add(
        Instance(
            id="sterlingoms01",
            name="sterling-oms",
            endpoint="http://sterling.local:8001",
            connection="direct",  # Mode A — pullable
            health="healthy",
            created_at="2026-07-09T09:00:00Z",
        )
    )

    # The instance's serve is stubbed; the panel is 100% real. `usage_box` grows the
    # cumulative rollup between syncs, exactly as a running instance would. `fetch_manifest`
    # reports "pre-delta" so each sync does a full pull (the stub has no manifest endpoint).
    usage_box = {"cur": _USAGE_T1}

    async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
        return _STATE

    async def fetch_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ManifestUnsupported("stub instance — full pull each sync")

    async def fetch_usage(endpoint: str, token_ref: str) -> dict[str, Any]:
        return usage_box["cur"]

    client = TestClient(
        create_app(
            registry,
            fetch_state=fetch_state,
            fetch_manifest=fetch_manifest,
            usage=fetch_usage,
        )
    )

    _bar("Before any sync — the Runs page (GET /usage) is empty")
    print(f"  GET /usage -> {client.get('/usage').json()}")

    _bar("Sync #1 — pulls /fleet/state AND /usage")
    body = client.post("/instances/sterlingoms01/sync").json()
    print(f"  pulled_usage = {body['pulled_usage']} model rows")
    _show_rollup(client)

    _bar("The instance keeps running — its cumulative /usage grows")
    usage_box["cur"] = _USAGE_T2
    print("  (kimi-k2 18000->31000 in-tokens, etc.)")

    _bar("Sync #2 — the rollup REFRESHES (snapshot), it does not double-count")
    body = client.post("/instances/sterlingoms01/sync").json()
    print(f"  pulled_usage = {body['pulled_usage']} model rows")
    _show_rollup(client)

    total_in = sum(r["input_tokens"] for r in client.get("/usage").json())
    print(f"\n  fleet input tokens = {total_in:,}  (= 31000+9800, the latest totals —")
    print("  not summed with sync #1's 18000+6500)")
    print("\nMode-A instances now show real usage on the Runs page, pulled at sync.")


if __name__ == "__main__":
    main()
