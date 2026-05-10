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

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, StatusCode, Tracer

from swarmkit_runtime.telemetry._config import TelemetryConfig

_ATTR_PREFIX = "swarmkit"


class SwarmKitTelemetry:
    """Instrumentation facade for SwarmKit runtime.

    Wraps OpenTelemetry trace API. When disabled (exporter=none),
    all methods are no-ops via the NoOp tracer.
    """

    def __init__(self, config: TelemetryConfig) -> None:
        self._config = config
        self._tracer: Tracer

        if not config.enabled or config.exporter == "none":
            self._tracer = trace.get_tracer("swarmkit-noop")
            return

        resource = Resource.create({"service.name": config.service_name})
        provider = TracerProvider(resource=resource)

        if config.exporter == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif config.exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter,
            )

            headers = dict(config.headers)
            if config.api_key:
                headers["Authorization"] = f"Bearer {config.api_key}"

            exporter = OTLPSpanExporter(
                endpoint=config.endpoint,
                headers=headers,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("swarmkit", schema_url=None)

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._config.exporter != "none"

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
