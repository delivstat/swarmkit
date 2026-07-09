"""Demo: fleet canary — monitor + control the runtime's canary from the panel (design 26, Layer A).

The runtime's CanaryRouter splits traffic between topology versions and tracks metrics. This shows
the panel federating that: read an instance's canary status, then promote a canary to 100% — the
real panel routes, with a stubbed instance's /canary (so no live serve is needed).

Run it:

    uv run python packages/control-plane/demos/fleet_canary.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._models import Instance

# The instance's canary router state (serve GET /canary) — 90/10 split, canary erroring a little.
_STATUS = {
    "enabled": True,
    "routes": [
        {
            "topology": "solution-design",
            "versions": [
                {"version": "1.0.0", "weight": 90},
                {
                    "version": "1.1.0",
                    "weight": 10,
                    "metrics": {"total_runs": 40, "failed_runs": 1, "error_rate": 0.025},
                },
            ],
        }
    ],
}


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "canary.sqlite"
    registry = SqliteRegistry(db)
    registry.add(
        Instance(
            id="sterlingoms01",
            name="sterling-oms",
            endpoint="http://sterling.local:8001",
            connection="direct",
            health="healthy",
            created_at="2026-07-09T09:00:00Z",
        )
    )

    promoted: dict[str, Any] = {}

    async def fetch_canary(endpoint: str, token_ref: str) -> dict[str, Any]:
        # After a promotion, the canary is at 100% (the instance would report this).
        if promoted:
            v100 = [{"version": "1.1.0", "weight": 100}]
            return {
                "enabled": True,
                "routes": [{"topology": "solution-design", "versions": v100}],
            }
        return _STATUS

    async def promote(endpoint: str, token_ref: str, topology: str, version: str) -> dict[str, Any]:
        promoted.update(topology=topology, version=version)
        return {"promoted": True, "topology": topology, "version": version}

    client = TestClient(create_app(registry, canary=fetch_canary, canary_promote=promote))

    _bar("Monitor — federated canary status (GET /instances/{id}/canary)")
    body = client.get("/instances/sterlingoms01/canary").json()
    print(f"  reachable={body['reachable']}")
    for v in body["canary"]["routes"][0]["versions"]:
        err = v.get("metrics", {}).get("error_rate")
        tag = "canary" if v["weight"] < 90 else "stable"
        print(
            f"    solution-design {v['version']}  weight={v['weight']}%  "
            f"error_rate={err if err is not None else '—'}  ({tag})"
        )

    _bar("Control — promote the canary to 100% (POST .../canary/solution-design/promote)")
    resp = client.post(
        "/instances/sterlingoms01/canary/solution-design/promote", json={"version": "1.1.0"}
    ).json()
    print(f"  {resp}")

    _bar("Re-read — the canary is now the sole version")
    after = client.get("/instances/sterlingoms01/canary").json()
    versions = after["canary"]["routes"][0]["versions"]
    weights = [(v["version"], f"{v['weight']}%") for v in versions]
    print(f"  {weights}")
    print("\nThe runtime's canary is now monitorable + controllable from the fleet.")


if __name__ == "__main__":
    main()
