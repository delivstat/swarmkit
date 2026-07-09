"""The process-wide active telemetry facade (design: runtime/otel-trace-export).

The OTel ``TracerProvider`` is process-global, so there's one :class:`SwarmKitTelemetry` per
process. ``get_telemetry`` lazily builds it from ``load_telemetry_config()`` (env / config file),
covering both ``swarmkit serve`` and CLI ``swarmkit run`` without either wiring it explicitly;
``configure_telemetry`` sets it eagerly (serve startup, so an operator sees the exporter at boot).
"""

from __future__ import annotations

from swarmkit_runtime.telemetry._config import TelemetryConfig, load_telemetry_config
from swarmkit_runtime.telemetry._tracer import SwarmKitTelemetry

# A holder dict (not a rebindable module global) so the setters don't need `global`.
_state: dict[str, SwarmKitTelemetry | None] = {"active": None}


def get_telemetry() -> SwarmKitTelemetry:
    """Return the active telemetry facade, building it from config on first use."""
    active = _state["active"]
    if active is None:
        active = SwarmKitTelemetry(load_telemetry_config())
        _state["active"] = active
    return active


def configure_telemetry(config: TelemetryConfig | None = None) -> SwarmKitTelemetry:
    """(Re)build the active telemetry facade from *config* (or the loaded config). Returns it."""
    telemetry = SwarmKitTelemetry(config or load_telemetry_config())
    _state["active"] = telemetry
    return telemetry


__all__ = ["configure_telemetry", "get_telemetry"]
