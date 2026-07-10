"""SwarmKitTelemetry — the instrumentation facade.

All OTel instrumentation goes through this class. Agent execution code
never imports opentelemetry directly — it calls methods on this facade,
which handles span creation, attribute setting, and exporter dispatch.

See design/details/opentelemetry-observability.md for the semantic
attribute namespace (swarmkit.*).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, StatusCode, Tracer

from swarmkit_runtime.telemetry._config import TelemetryConfig
from swarmkit_runtime.telemetry._metrics import init_metrics

_ATTR_PREFIX = "swarmkit"

# Metric export cadence. The reader flushes on this interval off the run's hot path, so a down
# collector never blocks or slows a run (same best-effort posture as BatchSpanProcessor for traces).
_METRIC_EXPORT_INTERVAL_MS = 15_000


def _metrics_endpoint(traces_endpoint: str) -> str:
    """Derive the OTLP metrics endpoint from the traces endpoint (one collector serves both).

    ``…/v1/traces`` → ``…/v1/metrics``; anything else is returned unchanged (custom collectors that
    don't use the standard OTLP HTTP path get their endpoint verbatim)."""
    suffix = "/v1/traces"
    if traces_endpoint.endswith(suffix):
        return traces_endpoint[: -len(suffix)] + "/v1/metrics"
    return traces_endpoint


@dataclass(frozen=True)
class RecordedSpan:
    """A span reconstructed from an already-finished run (design: runtime/otel-trace-export).

    Carries explicit start/end nanoseconds so a post-hoc export from ``RunTrace`` renders an
    accurate timeline in Jaeger. ``children`` nest under this span."""

    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    children: tuple[RecordedSpan, ...] = ()


class SwarmKitTelemetry:
    """Instrumentation facade for SwarmKit runtime.

    Wraps OpenTelemetry trace API. When disabled (exporter=none),
    all methods are no-ops via the NoOp tracer.
    """

    def __init__(
        self,
        config: TelemetryConfig,
        *,
        provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> None:
        self._config = config
        self._tracer: Tracer

        # An injected provider (tests: an in-memory exporter) short-circuits config-driven setup —
        # and never touches the process-global provider, so tests don't collide. A meter_provider
        # can be injected the same way to test the metrics emission path.
        if provider is not None:
            self._tracer = provider.get_tracer("swarmkit")
            if meter_provider is not None:
                init_metrics(config.service_name, meter_provider=meter_provider)
            return

        if not config.enabled or config.exporter == "none":
            self._tracer = trace.get_tracer("swarmkit-noop")
            return

        resource = Resource.create({"service.name": config.service_name})
        provider = TracerProvider(resource=resource)
        built_meter_provider: MeterProvider | None = None

        if config.exporter == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            built_meter_provider = self._build_meter_provider(resource, config, console=True)
        elif config.exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter,
            )

            headers = self._auth_headers(config)
            exporter = OTLPSpanExporter(
                endpoint=config.endpoint,
                headers=headers,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            built_meter_provider = self._build_meter_provider(resource, config, console=False)

        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("swarmkit", schema_url=None)

        # Metrics: mirror the trace provider so Prometheus/Grafana populate (design:
        # runtime/otel-metrics-export). Bind instruments to this provider explicitly so a later
        # reconfigure re-binds cleanly despite OTel's set-once global-provider semantics.
        if built_meter_provider is not None:
            metrics.set_meter_provider(built_meter_provider)
            init_metrics(config.service_name, meter_provider=built_meter_provider)

    @staticmethod
    def _auth_headers(config: TelemetryConfig) -> dict[str, str]:
        """Build the OTLP auth headers (shared by the span and metric exporters)."""
        headers = dict(config.headers)
        if config.api_key and config.api_key_header not in headers:
            prefix = "Bearer " if config.api_key_header == "Authorization" else ""
            headers[config.api_key_header] = f"{prefix}{config.api_key}"
        return headers

    @classmethod
    def _build_meter_provider(
        cls, resource: Resource, config: TelemetryConfig, *, console: bool
    ) -> MeterProvider:
        """A ``MeterProvider`` whose reader flushes on an interval (never on the hot path)."""
        from opentelemetry.sdk.metrics.export import (  # noqa: PLC0415
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )

        metric_exporter: Any
        if console:
            metric_exporter = ConsoleMetricExporter()
        else:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # noqa: PLC0415
                OTLPMetricExporter,
            )

            metric_exporter = OTLPMetricExporter(
                endpoint=_metrics_endpoint(config.endpoint),
                headers=cls._auth_headers(config),
            )
        reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=_METRIC_EXPORT_INTERVAL_MS
        )
        return MeterProvider(resource=resource, metric_readers=[reader])

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._config.exporter != "none"

    def export_run_spans(self, root: RecordedSpan) -> None:
        """Emit a finished run's span tree (design: runtime/otel-trace-export). Each
        ``RecordedSpan`` becomes an OTel span with its recorded start/end times and proper parent
        nesting, so a post-hoc export from ``RunTrace`` renders an accurate Jaeger timeline. No-op
        when disabled."""
        if not self.enabled:
            return
        self._emit_recorded(root, parent=None)

    def export_run_metrics(self, trace_obj: Any) -> None:
        """Emit run/step/tool metrics from a finished ``RunTrace`` (companion to
        :meth:`export_run_spans`; design: runtime/otel-metrics-export). Increments the run counter +
        records run duration, then per agent step the step counter, and per tool call the tool
        counter + duration. No-op when telemetry is disabled; best-effort — never raises on the run
        path (the reader ships these on its own interval, off the hot path)."""
        if not self.enabled:
            return
        from swarmkit_runtime.telemetry import _metrics  # noqa: PLC0415

        topology_id = getattr(trace_obj, "topology", "")
        _metrics.record_run_started(topology_id=topology_id)
        _metrics.record_run_completed(
            topology_id=topology_id, duration_ms=int(getattr(trace_obj, "duration_ms", 0))
        )
        for step in getattr(trace_obj, "agent_steps", []):
            _metrics.record_agent_step(agent_id=step.agent_id, topology_id=topology_id)
            for call in getattr(step, "tool_calls", []):
                _metrics.record_tool_call(
                    tool_name=call.tool_name,
                    status="error" if call.error else "ok",
                    duration_ms=int(call.duration_ms),
                )

    def _emit_recorded(self, rs: RecordedSpan, *, parent: Context | None) -> None:
        span = self._tracer.start_span(
            rs.name, context=parent, start_time=rs.start_ns, attributes=rs.attributes
        )
        if rs.error:
            span.set_status(StatusCode.ERROR, rs.error)
        child_ctx = trace.set_span_in_context(span)
        for child in rs.children:
            self._emit_recorded(child, parent=child_ctx)
        span.end(end_time=rs.end_ns)

    @contextmanager
    def start_run(
        self, *, topology_id: str, run_id: str, workspace_id: str = ""
    ) -> Generator[Span, None, None]:
        """Start a trace-level span for a topology run."""
        with self._tracer.start_as_current_span(
            "topology.run",
            attributes={
                f"{_ATTR_PREFIX}.topology.id": topology_id,
                f"{_ATTR_PREFIX}.run.id": run_id,
                f"{_ATTR_PREFIX}.workspace.id": workspace_id,
            },
        ) as span:
            yield span

    @contextmanager
    def start_agent_step(
        self,
        *,
        agent_id: str,
        step: int,
        archetype: str = "",
        role: str = "",
    ) -> Generator[Span, None, None]:
        """Start a child span for an agent execution step."""
        with self._tracer.start_as_current_span(
            f"agent.step.{agent_id}",
            attributes={
                f"{_ATTR_PREFIX}.agent.id": agent_id,
                f"{_ATTR_PREFIX}.agent.step": step,
                f"{_ATTR_PREFIX}.agent.archetype": archetype,
                f"{_ATTR_PREFIX}.agent.role": role,
            },
        ) as span:
            yield span

    @contextmanager
    def start_tool_call(
        self, *, tool_name: str, server_id: str = ""
    ) -> Generator[Span, None, None]:
        """Start a child span for a tool/MCP call."""
        with self._tracer.start_as_current_span(
            f"tool.call.{tool_name}",
            attributes={
                f"{_ATTR_PREFIX}.tool.name": tool_name,
                f"{_ATTR_PREFIX}.tool.server": server_id,
            },
        ) as span:
            yield span

    def record_tool_result(self, span: Span, *, status: str, error_type: str = "") -> None:
        """Set tool call result attributes on a span."""
        span.set_attribute(f"{_ATTR_PREFIX}.tool.status", status)
        if error_type:
            span.set_attribute(f"{_ATTR_PREFIX}.tool.error.type", error_type)
            span.set_status(StatusCode.ERROR, error_type)

    def record_governance_decision(
        self,
        *,
        decision: str,
        policy: str = "",
        scope: str = "",
    ) -> None:
        """Record a governance decision as an event on the current span."""
        current_span = trace.get_current_span()
        current_span.add_event(
            "governance.decision",
            attributes={
                f"{_ATTR_PREFIX}.governance.decision": decision,
                f"{_ATTR_PREFIX}.governance.policy": policy,
                f"{_ATTR_PREFIX}.governance.scope": scope,
            },
        )

    def record_model_usage(
        self,
        span: Span,
        *,
        provider: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Set model usage attributes on a span."""
        span.set_attribute(f"{_ATTR_PREFIX}.model.provider", provider)
        span.set_attribute(f"{_ATTR_PREFIX}.model.id", model)
        if tokens_in:
            span.set_attribute(f"{_ATTR_PREFIX}.model.tokens_in", tokens_in)
        if tokens_out:
            span.set_attribute(f"{_ATTR_PREFIX}.model.tokens_out", tokens_out)
        if cost_usd:
            span.set_attribute(f"{_ATTR_PREFIX}.model.cost_usd", cost_usd)

    def record_drift(
        self,
        *,
        score: float,
        threshold: float,
        action: str,
        exceeded: bool,
    ) -> None:
        """Record intent drift as an event on the current span."""
        current_span = trace.get_current_span()
        current_span.add_event(
            "intent.drift",
            attributes={
                f"{_ATTR_PREFIX}.drift.score": score,
                f"{_ATTR_PREFIX}.drift.threshold": threshold,
                f"{_ATTR_PREFIX}.drift.action": action,
                f"{_ATTR_PREFIX}.drift.exceeded": exceeded,
            },
        )

    def record_error(self, span: Span, *, error: Exception) -> None:
        """Record an exception on a span."""
        span.set_status(StatusCode.ERROR, str(error))
        span.record_exception(error)
