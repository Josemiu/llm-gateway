import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

logger = logging.getLogger(__name__)

# Safe to import and use anywhere (app/middleware/auth.py, rate_limit.py,
# routing/selector.py, routes/chat.py, services/usage_service.py,
# services/provider_stats_service.py) regardless of whether tracing is
# enabled: with no TracerProvider configured, the OpenTelemetry API falls
# back to a global no-op tracer, so start_as_current_span() is a harmless
# no-op (no exporter calls, negligible overhead) until setup_tracing() below
# actually configures one. That's what keeps tests and plain local dev
# (no Jaeger running) unaffected without scattering `if settings.otel_enabled`
# checks through every layer.
tracer = trace.get_tracer("llm-gateway")


def setup_tracing(app: FastAPI) -> None:
    if not settings.otel_enabled:
        return

    provider = TracerProvider(resource=Resource.create({"service.name": "llm-gateway"}))
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    logger.info(
        "OpenTelemetry tracing enabled, exporting to %s",
        settings.otel_exporter_otlp_endpoint,
    )
