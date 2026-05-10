"""Telemetry configuration — loaded from ~/.swarmkit/config.yaml or workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class TelemetryConfig:
    """Configuration for SwarmKit telemetry export.

    Loaded from ~/.swarmkit/config.yaml under the `telemetry:` key,
    or from workspace.yaml `telemetry:` block.
    """

    enabled: bool = False
    exporter: Literal["otlp", "console", "none"] = "none"
    endpoint: str = "http://localhost:4318/v1/traces"
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    sample_rate: float = 1.0
    send_prompts: bool = False
    service_name: str = "swarmkit"


def load_telemetry_config() -> TelemetryConfig:
    """Load telemetry config from ~/.swarmkit/config.yaml if it exists."""
    config_path = Path.home() / ".swarmkit" / "config.yaml"
    if not config_path.is_file():
        return TelemetryConfig()

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return TelemetryConfig()

    telemetry = data.get("telemetry", {})
    if not isinstance(telemetry, dict):
        return TelemetryConfig()

    return TelemetryConfig(
        enabled=bool(telemetry.get("enabled", False)),
        exporter=telemetry.get("exporter", "none"),
        endpoint=telemetry.get("endpoint", "http://localhost:4318/v1/traces"),
        api_key=telemetry.get("api_key"),
        headers=telemetry.get("headers", {}),
        sample_rate=float(telemetry.get("sample_rate", 1.0)),
        send_prompts=bool(telemetry.get("send_prompts", False)),
        service_name=telemetry.get("service_name", "swarmkit"),
    )
