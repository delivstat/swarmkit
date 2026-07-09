"""Demo: start a canary at runtime (design 26 Layer B).

The CanaryRouter used to be static — built from workspace.yaml at startup, only promote/rollback
after. `start_route` lets a fleet begin a canary rollout live: split traffic to a new version,
even on an instance that had no canary configured. This shows the bootstrap + split + metrics, all
in-process.

Run it:

    uv run python packages/runtime/demos/canary_start.py
"""

from __future__ import annotations

from swarmkit_runtime.canary._router import CanaryRouter


def _weights(router: CanaryRouter, topo: str) -> dict[str, float]:
    status = next(r for r in router.get_status() if r["topology"] == topo)
    return {v["version"]: v["weight"] for v in status["versions"]}


def main() -> None:
    # An instance with NO canary configured — the router is empty.
    router = CanaryRouter([])
    print(f"before: has_route('advisor') = {router.has_route('advisor')}")

    print("\nStart a canary: 15% of 'advisor' traffic to the new v2.1.0 (base v2.0.0):")
    router.start_route("advisor", base_version="2.0.0", canary_version="2.1.0", weight=15)
    print(f"  weights = {_weights(router, 'advisor')}")

    print("\nSimulate 200 runs — the router splits traffic by weight:")
    counts = {"2.0.0": 0, "2.1.0": 0}
    for _ in range(200):
        v = router.select("advisor")
        assert v is not None
        counts[v] += 1
        # the canary errors a little; the base is clean
        router.record_result("advisor", v, success=not (v == "2.1.0" and counts[v] % 20 == 0))
    print(f"  routed: {counts}  (~15% to the canary)")

    print("\nLive metrics (what the fleet Canary card + promote/rollback act on):")
    for v in next(r for r in router.get_status() if r["topology"] == "advisor")["versions"]:
        m = v.get("metrics", {})
        print(
            f"  v{v['version']}  weight={v['weight']}%  runs={m.get('total_runs', 0)}  "
            f"error_rate={m.get('error_rate', 0)}"
        )

    print("\nA canary can now be started fleet-wide without a restart (design 26 Layer B).")


if __name__ == "__main__":
    main()
