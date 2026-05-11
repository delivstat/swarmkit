"""OpenTelemetry instrumentation for SwarmKit runtime.

Provides the SwarmKitTelemetry facade — all OTel calls go through this.
Agent execution code never imports opentelemetry directly.

See design/details/opentelemetry-observability.md.
"""

from swarmkit_runtime.telemetry._config import TelemetryConfig, load_telemetry_config
from swarmkit_runtime.telemetry._metrics import (
    init_metrics,
    record_agent_step,
    record_approval_wait,
    record_governance_decision,
    record_run_completed,
    record_run_started,
    record_tool_call,
)
from swarmkit_runtime.telemetry._ring_buffer import PromptRingBuffer
from swarmkit_runtime.telemetry._tracer import SwarmKitTelemetry

__all__ = [
    "PromptRingBuffer",
    "SwarmKitTelemetry",
    "TelemetryConfig",
    "init_metrics",
    "load_telemetry_config",
    "record_agent_step",
    "record_approval_wait",
    "record_governance_decision",
    "record_run_completed",
    "record_run_started",
    "record_tool_call",
]
