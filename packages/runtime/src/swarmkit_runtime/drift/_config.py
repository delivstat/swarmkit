"""Intent monitoring configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IntentMonitoringConfig:
    """Configuration for intent drift detection on an agent or topology.

    Declared in topology YAML:
        intent_monitoring:
          enabled: true
          threshold: 0.25
          on_drift: nudge
    """

    enabled: bool = False
    threshold: float = 0.25
    on_drift: Literal["log", "warn", "nudge"] = "log"

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> IntentMonitoringConfig:
        """Parse from a YAML dict. Returns disabled config if None."""
        if not data:
            return cls()
        raw_threshold = data.get("threshold", 0.25)
        raw_on_drift = data.get("on_drift", "log")
        return cls(
            enabled=bool(data.get("enabled", False)),
            threshold=float(raw_threshold),  # type: ignore[arg-type]
            on_drift=str(raw_on_drift),  # type: ignore[arg-type]
        )
