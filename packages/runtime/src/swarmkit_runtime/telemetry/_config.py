"""Telemetry configuration — loaded from env vars, config file, or defaults.

Resolution order (highest priority wins):
  1. Environment variables: SWARMKIT_OTEL_ENDPOINT, SWARMKIT_OTEL_EXPORTER, etc.
  2. Config file: ~/.swarmkit/config.yaml → telemetry: block
  3. Defaults (disabled, zero overhead)

For quick local testing:
  SWARMKIT_OTEL_EXPORTER=console swarmkit run ...

For production (Rynko, Grafana, Jaeger):
  ~/.swarmkit/config.yaml with full telemetry block.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class TelemetryConfig:
    """Configuration for SwarmKit telemetry export.

    Resolution: env vars override config file; config file overrides defaults.

    The ``headers`` dict is passed directly to the OTLP exporter. Use it
    to set whatever auth header your backend expects:
      - Rynko: {"Authorization": "Bearer rk-..."}
      - Grafana: {"Authorization": "Basic <base64>"}
      - Honeycomb: {"x-honeycomb-team": "<key>"}

    The ``api_key`` field is a convenience shortcut — if set and headers
    doesn't already contain an auth header, it's added as
    ``Authorization: Bearer <api_key>``. For non-Bearer backends, use
    ``headers`` directly and leave ``api_key`` empty.
    """

    enabled: bool = False
    exporter: Literal["otlp", "console", "none"] = "none"
    endpoint: str = "http://localhost:4318/v1/traces"
    api_key: str | None = None
    api_key_header: str = "Authorization"
    headers: dict[str, str] = field(default_factory=dict)
    sample_rate: float = 1.0
    send_prompts: bool = False
    service_name: str = "swarmkit"


def load_telemetry_config() -> TelemetryConfig:
    """Load telemetry config with env var overrides.

    Env vars (override config file when set):
      SWARMKIT_OTEL_EXPORTER  — "console", "otlp", or "none"
      SWARMKIT_OTEL_ENDPOINT  — OTLP collector URL
      SWARMKIT_OTEL_API_KEY   — API key (added as Bearer token by default)
      SWARMKIT_OTEL_HEADERS   — comma-separated key=value pairs
    """
    file_config = _load_from_file()

    env_exporter = os.environ.get("SWARMKIT_OTEL_EXPORTER")
    env_endpoint = os.environ.get("SWARMKIT_OTEL_ENDPOINT")
    env_api_key = os.environ.get("SWARMKIT_OTEL_API_KEY")
    env_headers = os.environ.get("SWARMKIT_OTEL_HEADERS")

    exporter = env_exporter or file_config.exporter
    endpoint = env_endpoint or file_config.endpoint
    api_key = env_api_key or file_config.api_key

    headers = dict(file_config.headers)
    if env_headers:
        for pair in env_headers.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()

    enabled = exporter != "none" if env_exporter else file_config.enabled

    return TelemetryConfig(
        enabled=enabled,
        exporter=exporter,  # type: ignore[arg-type]
        endpoint=endpoint,
        api_key=api_key,
        api_key_header=file_config.api_key_header,
        headers=headers,
        sample_rate=file_config.sample_rate,
        send_prompts=file_config.send_prompts,
        service_name=file_config.service_name,
    )


def _load_from_file() -> TelemetryConfig:
    """Load from ~/.swarmkit/config.yaml if it exists."""
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
        api_key_header=telemetry.get("api_key_header", "Authorization"),
        headers=telemetry.get("headers", {}),
        sample_rate=float(telemetry.get("sample_rate", 1.0)),
        send_prompts=bool(telemetry.get("send_prompts", False)),
        service_name=telemetry.get("service_name", "swarmkit"),
    )
