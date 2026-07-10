"""OTel metrics for SwarmKit runtime.

Counters and histograms for operational monitoring. Emitted via the
OpenTelemetry metrics API alongside traces.

See design/details/opentelemetry-observability.md.
"""

from __future__ import annotations

from opentelemetry import metrics

_ATTR_PREFIX = "swarmkit"

_meter: metrics.Meter | None = None

_runs_total: metrics.Counter | None = None
_agent_steps_total: metrics.Counter | None = None
_tool_calls_total: metrics.Counter | None = None
_governance_decisions_total: metrics.Counter | None = None

_run_duration_ms: metrics.Histogram | None = None
_tool_duration_ms: metrics.Histogram | None = None
_approval_wait_ms: metrics.Histogram | None = None

_drift_score: metrics.Histogram | None = None
_drift_breaches_total: metrics.Counter | None = None

_compression_bytes_saved_total: metrics.Counter | None = None
_compression_ratio: metrics.Histogram | None = None


def init_metrics(
    service_name: str = "swarmkit",
    *,
    meter_provider: metrics.MeterProvider | None = None,
) -> None:
    """Initialize OTel metrics instruments. Call once at startup, after a ``MeterProvider`` is set.

    ``meter_provider`` binds the instruments to a specific provider rather than the process-global
    one. Passing it explicitly (a) sidesteps OTel's set-once global-provider semantics on a
    reconfigure and (b) makes the emission path unit-testable with an in-memory reader.
    """
    global _meter  # noqa: PLW0603
    global _runs_total, _agent_steps_total, _tool_calls_total  # noqa: PLW0603
    global _governance_decisions_total  # noqa: PLW0603
    global _run_duration_ms, _tool_duration_ms, _approval_wait_ms  # noqa: PLW0603
    global _drift_score, _drift_breaches_total  # noqa: PLW0603
    global _compression_bytes_saved_total, _compression_ratio  # noqa: PLW0603

    provider = meter_provider if meter_provider is not None else metrics.get_meter_provider()
    _meter = provider.get_meter(service_name)

    _runs_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.runs.total",
        description="Total topology runs",
        unit="1",
    )
    _agent_steps_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.agent.steps.total",
        description="Total agent execution steps across all runs",
        unit="1",
    )
    _tool_calls_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.tool.calls.total",
        description="Total tool/MCP invocations",
        unit="1",
    )
    _governance_decisions_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.governance.decisions.total",
        description="Total governance policy decisions",
        unit="1",
    )

    _run_duration_ms = _meter.create_histogram(
        f"{_ATTR_PREFIX}.runs.duration_ms",
        description="Topology run duration in milliseconds",
        unit="ms",
    )
    _tool_duration_ms = _meter.create_histogram(
        f"{_ATTR_PREFIX}.tool.duration_ms",
        description="Tool call duration in milliseconds",
        unit="ms",
    )
    _approval_wait_ms = _meter.create_histogram(
        f"{_ATTR_PREFIX}.approval.wait_ms",
        description="Human approval wait time in milliseconds",
        unit="ms",
    )

    _drift_score = _meter.create_histogram(
        f"{_ATTR_PREFIX}.agent.drift.score",
        description="Intent drift score per agent step (0 = on-track, 1 = fully drifted)",
        unit="1",
    )
    _drift_breaches_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.agent.drift.breaches.total",
        description="Total drift threshold breaches across all agents",
        unit="1",
    )

    _compression_bytes_saved_total = _meter.create_counter(
        f"{_ATTR_PREFIX}.compression.bytes_saved.total",
        description="Total characters saved by read-side context compression",
        unit="1",
    )
    _compression_ratio = _meter.create_histogram(
        f"{_ATTR_PREFIX}.compression.ratio",
        description="Compressed/original size ratio per tool result (lower is better)",
        unit="1",
    )


def record_run_started(*, topology_id: str) -> None:
    """Increment run counter."""
    if _runs_total is not None:
        _runs_total.add(1, {"topology_id": topology_id})


def record_run_completed(*, topology_id: str, duration_ms: int) -> None:
    """Record run duration."""
    if _run_duration_ms is not None:
        _run_duration_ms.record(duration_ms, {"topology_id": topology_id})


def record_agent_step(*, agent_id: str, topology_id: str) -> None:
    """Increment agent step counter."""
    if _agent_steps_total is not None:
        _agent_steps_total.add(1, {"agent_id": agent_id, "topology_id": topology_id})


def record_tool_call(*, tool_name: str, status: str, duration_ms: int = 0) -> None:
    """Increment tool call counter and record duration."""
    if _tool_calls_total is not None:
        _tool_calls_total.add(1, {"tool_name": tool_name, "status": status})
    if _tool_duration_ms is not None and duration_ms > 0:
        _tool_duration_ms.record(duration_ms, {"tool_name": tool_name, "status": status})


def record_governance_decision(*, decision: str, scope: str = "") -> None:
    """Increment governance decision counter."""
    if _governance_decisions_total is not None:
        _governance_decisions_total.add(1, {"decision": decision, "scope": scope})


def record_approval_wait(*, scope: str, wait_ms: int) -> None:
    """Record human approval wait time."""
    if _approval_wait_ms is not None:
        _approval_wait_ms.record(wait_ms, {"scope": scope})


def record_drift_score(*, agent_id: str, score: float) -> None:
    """Record a drift score observation."""
    if _drift_score is not None:
        _drift_score.record(score, {"agent_id": agent_id})


def record_drift_breach(*, agent_id: str) -> None:
    """Increment drift breach counter."""
    if _drift_breaches_total is not None:
        _drift_breaches_total.add(1, {"agent_id": agent_id})


def record_compression(*, tool_name: str, backend: str, bytes_in: int, bytes_out: int) -> None:
    """Record read-side compression savings for one tool result."""
    attrs = {"tool_name": tool_name, "backend": backend}
    if _compression_bytes_saved_total is not None:
        _compression_bytes_saved_total.add(max(0, bytes_in - bytes_out), attrs)
    if _compression_ratio is not None and bytes_in > 0:
        _compression_ratio.record(bytes_out / bytes_in, attrs)
