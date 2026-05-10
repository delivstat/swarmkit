"""OpenTelemetry instrumentation for SwarmKit runtime.

Provides the SwarmKitTelemetry facade — all OTel calls go through this.
Agent execution code never imports opentelemetry directly.

See design/details/opentelemetry-observability.md.
"""

from swarmkit_runtime.telemetry._config import TelemetryConfig, load_telemetry_config
from swarmkit_runtime.telemetry._tracer import SwarmKitTelemetry

__all__ = [
    "SwarmKitTelemetry",
    "TelemetryConfig",
    "load_telemetry_config",
]
