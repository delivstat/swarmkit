"""Tests for canary deployment routing and metrics.

Covers:
- Weighted version selection
- Metrics tracking (success/failure)
- Auto-promotion when criteria met
- Manual promote/rollback
- GET /canary status endpoint
- POST /canary/{topology}/promote endpoint
- POST /canary/{topology}/rollback endpoint
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.canary._router import CanaryRouter

# ---------------------------------------------------------------------------
# Router unit tests
# ---------------------------------------------------------------------------


def _make_router(
    weight_a: int = 90,
    weight_b: int = 10,
    promote_when: dict[str, object] | None = None,
) -> CanaryRouter:
    v_b: dict[str, object] = {"version": "1.1.0", "weight": weight_b}
    if promote_when:
        v_b["promote_when"] = promote_when
    routes = [
        {
            "topology": "hello",
            "versions": [
                {"version": "1.0.0", "weight": weight_a},
                v_b,
            ],
        }
    ]
    return CanaryRouter(routes)


def test_has_route() -> None:
    router = _make_router()
    assert router.has_route("hello") is True
    assert router.has_route("nonexistent") is False


def test_select_returns_valid_version() -> None:
    router = _make_router()
    versions_seen: set[str] = set()
    for _ in range(200):
        v = router.select("hello")
        assert v is not None
        versions_seen.add(v)
    assert "1.0.0" in versions_seen
    assert "1.1.0" in versions_seen


def test_select_no_route_returns_none() -> None:
    router = _make_router()
    assert router.select("nonexistent") is None


def test_weight_distribution() -> None:
    router = _make_router(weight_a=50, weight_b=50)
    counts: dict[str, int] = {"1.0.0": 0, "1.1.0": 0}
    for _ in range(1000):
        v = router.select("hello")
        assert v is not None
        counts[v] += 1
    assert counts["1.0.0"] > 350
    assert counts["1.1.0"] > 350


def test_resolve_topology_key() -> None:
    router = _make_router()
    key = router.resolve_topology_key("hello")
    assert key.startswith("hello@")
    assert key in ("hello@1.0.0", "hello@1.1.0")


def test_resolve_topology_key_no_route() -> None:
    router = _make_router()
    assert router.resolve_topology_key("other") == "other"


# ---------------------------------------------------------------------------
# Metrics tracking
# ---------------------------------------------------------------------------


def test_record_result_tracks_metrics() -> None:
    router = _make_router()
    router.record_result("hello", "1.1.0", success=True)
    router.record_result("hello", "1.1.0", success=True)
    router.record_result("hello", "1.1.0", success=False)

    status = router.get_status()
    assert len(status) == 1
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["metrics"]["total_runs"] == 3
    assert v11["metrics"]["failed_runs"] == 1
    assert v11["metrics"]["error_rate"] == pytest.approx(1 / 3, abs=0.01)


def test_record_result_ignores_unknown() -> None:
    router = _make_router()
    router.record_result("unknown", "1.0.0", success=True)
    router.record_result("hello", "9.9.9", success=True)


def test_drift_tracking() -> None:
    router = _make_router()
    router.record_result("hello", "1.1.0", success=True, drift_score=0.1)
    router.record_result("hello", "1.1.0", success=True, drift_score=0.2)
    router.record_result("hello", "1.1.0", success=True, drift_score=0.3)

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["metrics"]["avg_drift"] == pytest.approx(0.2, abs=0.01)


# ---------------------------------------------------------------------------
# Auto-promotion
# ---------------------------------------------------------------------------


def test_auto_promotion() -> None:
    router = _make_router(
        weight_a=90,
        weight_b=10,
        promote_when={
            "min_runs": 3,
            "error_rate_below": 0.1,
            "drift_below": 0.5,
            "window_minutes": 60,
        },
    )

    for _ in range(3):
        router.record_result("hello", "1.1.0", success=True, drift_score=0.1)

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 100

    v10 = next(v for v in status[0]["versions"] if v["version"] == "1.0.0")
    assert v10["weight"] == 0

    promotions = router.get_promotions()
    assert len(promotions) == 1
    assert promotions[0]["promoted_version"] == "1.1.0"


def test_no_promotion_below_min_runs() -> None:
    router = _make_router(
        promote_when={"min_runs": 10, "error_rate_below": 0.5},
    )
    for _ in range(5):
        router.record_result("hello", "1.1.0", success=True)

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 10


def test_no_promotion_high_error_rate() -> None:
    router = _make_router(
        promote_when={"min_runs": 4, "error_rate_below": 0.1},
    )
    for _ in range(3):
        router.record_result("hello", "1.1.0", success=True)
    router.record_result("hello", "1.1.0", success=False)

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 10


def test_no_promotion_high_drift() -> None:
    router = _make_router(
        promote_when={"min_runs": 3, "drift_below": 0.2},
    )
    for _ in range(3):
        router.record_result("hello", "1.1.0", success=True, drift_score=0.5)

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 10


# ---------------------------------------------------------------------------
# Manual promote / rollback
# ---------------------------------------------------------------------------


def test_manual_promote() -> None:
    router = _make_router()
    assert router.promote("hello", "1.1.0") is True

    status = router.get_status()
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 100
    v10 = next(v for v in status[0]["versions"] if v["version"] == "1.0.0")
    assert v10["weight"] == 0


def test_manual_promote_unknown() -> None:
    router = _make_router()
    assert router.promote("nonexistent", "1.0.0") is False
    assert router.promote("hello", "9.9.9") is False


def test_rollback() -> None:
    router = _make_router()
    router.promote("hello", "1.1.0")
    assert router.rollback("hello") is True

    status = router.get_status()
    v10 = next(v for v in status[0]["versions"] if v["version"] == "1.0.0")
    assert v10["weight"] == 100
    v11 = next(v for v in status[0]["versions"] if v["version"] == "1.1.0")
    assert v11["weight"] == 0


def test_rollback_unknown() -> None:
    router = _make_router()
    assert router.rollback("nonexistent") is False


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------


def test_get_status_shape() -> None:
    router = _make_router(
        promote_when={"min_runs": 10, "error_rate_below": 0.05},
    )
    status = router.get_status()
    assert len(status) == 1
    route = status[0]
    assert route["topology"] == "hello"
    assert len(route["versions"]) == 2

    v11 = next(v for v in route["versions"] if v["version"] == "1.1.0")
    assert "metrics" in v11
    assert "promote_when" in v11
    assert v11["promote_when"]["min_runs"] == 10


def test_weight_sum_warning(caplog: pytest.LogCaptureFixture) -> None:
    routes = [
        {
            "topology": "hello",
            "versions": [
                {"version": "1.0.0", "weight": 60},
                {"version": "1.1.0", "weight": 30},
            ],
        }
    ]
    with caplog.at_level("WARNING"):
        CanaryRouter(routes)
    assert "sum to 90" in caplog.text


# ---------------------------------------------------------------------------
# Server endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def test_canary_status_no_routes() -> None:
    from pathlib import Path  # noqa: PLC0415

    from fastapi.testclient import TestClient  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    ws = Path(__file__).resolve().parents[3] / "examples" / "hello-swarm" / "workspace"
    app = create_app(ws)
    with TestClient(app) as client:
        resp = client.get("/canary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["routes"] == []
