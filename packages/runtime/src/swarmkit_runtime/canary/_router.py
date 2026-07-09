"""Canary deployment router for SwarmKit serve mode.

Weighted traffic splitting between topology versions with metrics tracking
and optional auto-promotion. See design/details/canary-deployments.md.
"""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("swarmkit.canary")


@dataclass
class VersionMetrics:
    """Runtime metrics for a single topology version within a canary route."""

    version: str
    total_runs: int = 0
    failed_runs: int = 0
    drift_scores: list[float] = field(default_factory=list)
    window_start: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def error_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.failed_runs / self.total_runs

    @property
    def avg_drift(self) -> float:
        if not self.drift_scores:
            return 0.0
        return sum(self.drift_scores) / len(self.drift_scores)

    def reset_window(self) -> None:
        self.total_runs = 0
        self.failed_runs = 0
        self.drift_scores = []
        self.window_start = datetime.now(UTC)


@dataclass
class CanaryRoute:
    """A canary route for a single topology with version weights."""

    topology: str
    versions: list[VersionEntry] = field(default_factory=list)

    def select_version(self) -> str:
        roll = random.randint(1, 100)
        cumulative = 0
        for v in self.versions:
            cumulative += v.weight
            if roll <= cumulative:
                return v.version
        return self.versions[-1].version


@dataclass
class VersionEntry:
    """A version with its weight and optional promotion criteria."""

    version: str
    weight: int
    promote_when: PromoteCriteria | None = None


@dataclass
class PromoteCriteria:
    """Conditions for auto-promotion of a canary version."""

    min_runs: int = 50
    error_rate_below: float = 0.05
    drift_below: float = 0.30
    window_minutes: int = 60


class CanaryRouter:
    """Routes topology requests across versioned canary deployments.

    Thread-safe. Metrics are tracked per-version and evaluated against
    promotion criteria on each ``record_result`` call.

    Parameters
    ----------
    routes:
        List of canary route config dicts parsed from workspace.yaml.
    available_versions:
        Mapping of ``topology_name`` → set of available version strings.
        Used to validate that configured versions actually exist.
    """

    def __init__(
        self,
        routes: list[dict[str, Any]],
        available_versions: dict[str, set[str]] | None = None,
    ) -> None:
        self._routes: dict[str, CanaryRoute] = {}
        self._metrics: dict[str, dict[str, VersionMetrics]] = {}
        self._lock = threading.Lock()
        self._promotions: list[dict[str, str]] = []

        for route_cfg in routes:
            topo = route_cfg["topology"]
            versions = []
            for v_cfg in route_cfg.get("versions", []):
                pw = v_cfg.get("promote_when")
                criteria = None
                if pw:
                    criteria = PromoteCriteria(
                        min_runs=pw.get("min_runs", 50),
                        error_rate_below=pw.get("error_rate_below", 0.05),
                        drift_below=pw.get("drift_below", 0.30),
                        window_minutes=pw.get("window_minutes", 60),
                    )
                version = v_cfg["version"]

                if (
                    available_versions
                    and topo in available_versions
                    and version not in available_versions[topo]
                ):
                    logger.warning(
                        "Canary route %r references version %r but only %r are available",
                        topo,
                        version,
                        sorted(available_versions[topo]),
                    )

                versions.append(
                    VersionEntry(version=version, weight=v_cfg["weight"], promote_when=criteria)
                )

            total_weight = sum(v.weight for v in versions)
            if total_weight != 100:
                logger.warning(
                    "Canary route %r weights sum to %d (expected 100)",
                    topo,
                    total_weight,
                )

            self._routes[topo] = CanaryRoute(topology=topo, versions=versions)
            self._metrics[topo] = {v.version: VersionMetrics(version=v.version) for v in versions}

        if self._routes:
            logger.info(
                "CanaryRouter initialized with %d route(s): %s",
                len(self._routes),
                ", ".join(f"{t} ({len(r.versions)} versions)" for t, r in self._routes.items()),
            )

    def has_route(self, topology_name: str) -> bool:
        return topology_name in self._routes

    def select(self, topology_name: str) -> str | None:
        """Select a version for the given topology. Returns version string or None if no route."""
        route = self._routes.get(topology_name)
        if route is None:
            return None
        return route.select_version()

    def resolve_topology_key(self, topology_name: str) -> str:
        """Return the version-qualified topology key (e.g. ``hello@1.1.0``)."""
        version = self.select(topology_name)
        if version is None:
            return topology_name
        return f"{topology_name}@{version}"

    def record_result(
        self,
        topology_name: str,
        version: str,
        *,
        success: bool,
        drift_score: float | None = None,
    ) -> None:
        """Record a run result for metrics tracking and promotion evaluation."""
        with self._lock:
            topo_metrics = self._metrics.get(topology_name)
            if topo_metrics is None:
                return
            vm = topo_metrics.get(version)
            if vm is None:
                return

            now = datetime.now(UTC)
            route = self._routes.get(topology_name)
            if route is None:
                return

            entry = next((v for v in route.versions if v.version == version), None)
            if entry is None:
                return

            window_minutes = entry.promote_when.window_minutes if entry.promote_when else 60
            elapsed = (now - vm.window_start).total_seconds() / 60
            if elapsed > window_minutes:
                vm.reset_window()

            vm.total_runs += 1
            if not success:
                vm.failed_runs += 1
            if drift_score is not None:
                vm.drift_scores.append(drift_score)

            if entry.promote_when:
                self._check_promotion(topology_name, version, entry, vm)

    def _check_promotion(
        self,
        topology_name: str,
        version: str,
        entry: VersionEntry,
        metrics: VersionMetrics,
    ) -> None:
        """Check if canary version meets promotion criteria."""
        criteria = entry.promote_when
        if criteria is None:
            return

        if metrics.total_runs < criteria.min_runs:
            return
        if metrics.error_rate >= criteria.error_rate_below:
            return
        if metrics.avg_drift >= criteria.drift_below and metrics.drift_scores:
            return

        route = self._routes[topology_name]
        if entry.weight >= 100:
            return

        old_weights = {v.version: v.weight for v in route.versions}
        for v in route.versions:
            v.weight = 100 if v.version == version else 0

        self._promotions.append(
            {
                "topology": topology_name,
                "promoted_version": version,
                "old_weights": str(old_weights),
                "metrics": (
                    f"runs={metrics.total_runs}, "
                    f"error_rate={metrics.error_rate:.3f}, "
                    f"avg_drift={metrics.avg_drift:.3f}"
                ),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        logger.info(
            "Canary promoted: %s → v%s (runs=%d, error_rate=%.3f, avg_drift=%.3f)",
            topology_name,
            version,
            metrics.total_runs,
            metrics.error_rate,
            metrics.avg_drift,
        )

    def get_status(self) -> list[dict[str, Any]]:
        """Return current canary status for all routes."""
        with self._lock:
            result = []
            for topo, route in self._routes.items():
                versions = []
                for v in route.versions:
                    vm = self._metrics[topo].get(v.version)
                    entry: dict[str, Any] = {
                        "version": v.version,
                        "weight": v.weight,
                    }
                    if vm:
                        entry["metrics"] = {
                            "total_runs": vm.total_runs,
                            "failed_runs": vm.failed_runs,
                            "error_rate": round(vm.error_rate, 4),
                            "avg_drift": round(vm.avg_drift, 4),
                            "window_start": vm.window_start.isoformat(),
                        }
                    if v.promote_when:
                        entry["promote_when"] = {
                            "min_runs": v.promote_when.min_runs,
                            "error_rate_below": v.promote_when.error_rate_below,
                            "drift_below": v.promote_when.drift_below,
                            "window_minutes": v.promote_when.window_minutes,
                        }
                    versions.append(entry)
                result.append({"topology": topo, "versions": versions})
            return result

    def get_promotions(self) -> list[dict[str, str]]:
        """Return the history of auto-promotions."""
        with self._lock:
            return list(self._promotions)

    def promote(self, topology_name: str, version: str) -> bool:
        """Manually promote a version to 100% traffic."""
        with self._lock:
            route = self._routes.get(topology_name)
            if route is None:
                return False
            found = False
            for v in route.versions:
                if v.version == version:
                    v.weight = 100
                    found = True
                else:
                    v.weight = 0
            if found:
                logger.info("Manual canary promote: %s → v%s", topology_name, version)
            return found

    def rollback(self, topology_name: str) -> bool:
        """Roll back to the first version (reset weights to original config)."""
        with self._lock:
            route = self._routes.get(topology_name)
            if route is None:
                return False
            if len(route.versions) < 2:
                return False
            route.versions[0].weight = 100
            for v in route.versions[1:]:
                v.weight = 0
            logger.info(
                "Canary rollback: %s → v%s",
                topology_name,
                route.versions[0].version,
            )
            return True

    def start_route(
        self,
        topology_name: str,
        base_version: str,
        canary_version: str,
        weight: int,
        promote_when: dict[str, Any] | None = None,
    ) -> None:
        """Start (or replace) a canary route at runtime (design 26 Layer B): split *weight*% of
        traffic to *canary_version*, the rest to *base_version*. Lets the fleet begin a canary
        rollout without a restart. *weight* is 1-99; the canary version's artifact must already be
        deployed to this instance. Not persisted to workspace.yaml — a restart reverts to the
        declared config."""
        if not 0 < weight < 100:
            raise ValueError("canary weight must be between 1 and 99")
        if base_version == canary_version:
            raise ValueError("base and canary versions must differ")
        pc = None
        if promote_when:
            pc = PromoteCriteria(
                min_runs=promote_when.get("min_runs", 50),
                error_rate_below=promote_when.get("error_rate_below", 0.05),
                drift_below=promote_when.get("drift_below", 0.30),
                window_minutes=promote_when.get("window_minutes", 60),
            )
        versions = [
            VersionEntry(version=base_version, weight=100 - weight, promote_when=None),
            VersionEntry(version=canary_version, weight=weight, promote_when=pc),
        ]
        with self._lock:
            self._routes[topology_name] = CanaryRoute(topology=topology_name, versions=versions)
            self._metrics[topology_name] = {
                v.version: VersionMetrics(version=v.version) for v in versions
            }
        logger.info(
            "Canary started: %s — base v%s (%d%%) / canary v%s (%d%%)",
            topology_name,
            base_version,
            100 - weight,
            canary_version,
            weight,
        )


__all__ = ["CanaryRouter"]
