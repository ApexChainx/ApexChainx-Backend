"""OpenTelemetry tracing exporter and span propagation (BE-062).

Integrates OTel SDK with:
- OTLP gRPC exporter for production traces
- Auto-instrumentation for FastAPI, SQLAlchemy, httpx, redis
- W3C trace context propagation (traceparent) for distributed tracing
- Correlation ID integration with OTel spans
- Configurable sampling (1% production, 100% development)
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider, sampling
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-level tracer
tracer = trace.get_tracer(__name__)

# Default service attributes
_service_resource = Resource.create(
    {
        SERVICE_NAME: settings.PROJECT_NAME,
        SERVICE_VERSION: settings.VERSION,
        "deployment.environment": settings.ENVIRONMENT,
    }
)


def _create_sampler() -> sampling.Sampler:
    """Create a sampler based on environment configuration.

    Production: 1% parent-based sampling
    Development/Local: always-on sampling
    """
    env = settings.ENVIRONMENT.lower()
    if env in ("production", "prod", "staging"):
        return sampling.ParentBased(
            root=sampling.TraceIdRatioBased(0.01),  # 1% production sampling
        )
    return sampling.ALWAYS_ON


def _create_span_processor() -> BatchSpanProcessor:
    """Create a span processor with OTLP exporter when configured.

    Falls back to console exporter for local development.
    """
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    otlp_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")

    if otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers=otlp_headers,
        )
        logger.info("OTLP exporter configured at %s", otlp_endpoint)
    else:
        exporter = ConsoleSpanExporter()
        logger.info("Using console span exporter for development")

    return BatchSpanProcessor(exporter)


def init_tracing() -> TracerProvider:
    """Initialize OpenTelemetry tracing.

    Sets up:
    - TracerProvider with resource attributes
    - BatchSpanProcessor with OTLP or console exporter
    - W3C Trace Context propagation
    - Auto-instrumentation for supported libraries

    Returns:
        Configured TracerProvider instance.

    Note:
        Call this once at application startup before any instrumentation.
        Subsequent calls are no-ops if tracing is already initialized.
    """
    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider) and hasattr(current_provider, "_active_span_processor"):
        if getattr(current_provider, "_active_span_processor", None):
            logger.debug("Tracing already initialized, skipping.")
            return current_provider

    sampler = _create_sampler()
    provider = TracerProvider(resource=_service_resource, sampler=sampler)

    span_processor = _create_span_processor()
    provider.add_span_processor(span_processor)

    trace.set_tracer_provider(provider)

    # Set W3C Trace Context propagation (traceparent/tracestate)
    set_global_textmap(TraceContextTextMapPropagator())

    logger.info(
        "OpenTelemetry tracing initialized (env=%s, sampler=%s)",
        settings.ENVIRONMENT,
        type(sampler).__name__,
    )

    return provider


def instrument_app(app) -> None:
    """Apply auto-instrumentation to a FastAPI application.

    Instruments:
    - FastAPI (request/response spans)
    - SQLAlchemy (database query spans)
    - httpx (outbound HTTP request spans)
    - Redis (cache/queue operation spans)
    """
    # FastAPI instrumentation with request/response capture
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/health,/health/liveness,/health/readiness,/metrics",
        server_request_hook=_server_request_hook,
        client_response_hook=_client_response_hook,
    )

    # SQLAlchemy instrumentation captures all DB queries
    try:
        from app.db.session import engine

        SQLAlchemyInstrumentor().instrument(engine=engine)
        logger.info("SQLAlchemy instrumentation applied")
    except Exception as exc:
        logger.warning("Failed to instrument SQLAlchemy: %s", exc)

    # httpx instrumentation captures outbound API calls
    HTTPXClientInstrumentor().instrument()
    logger.info("httpx instrumentation applied")

    # Redis instrumentation captures cache and queue operations
    try:
        RedisInstrumentor().instrument()
        logger.info("Redis instrumentation applied")
    except Exception as exc:
        logger.warning("Failed to instrument Redis: %s", exc)

    logger.info("Auto-instrumentation applied to FastAPI, SQLAlchemy, httpx, Redis")


def _server_request_hook(span, scope):
    """Enrich server spans with correlation ID and request metadata."""
    from app.utils.correlation_ctx import get_correlation_id

    corr_id = get_correlation_id()
    if corr_id:
        span.set_attribute("correlation.id", corr_id)
        span.set_attribute("trace.id", corr_id)


def _client_response_hook(span, request, response):
    """Enrich client spans with response metadata."""
    if response is not None:
        span.set_attribute("http.status_code", response.status_code)


def get_current_traceparent() -> Optional[str]:
    """Get the current trace context as a traceparent header value.

    Returns:
        W3C traceparent string (e.g., '00-{trace_id}-{span_id}-01') or None.

    Used for injecting trace context into webhook deliveries
    and outbound API calls for distributed tracing.
    """
    current_span = trace.get_current_span()
    if not current_span or not current_span.get_span_context().is_valid:
        return None

    span_context = current_span.get_span_context()
    return f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-0{span_context.trace_flags:02x}"


def traced(name: Optional[str] = None, attributes: Optional[dict[str, Any]] = None):
    """Decorator to trace a function with a span.

    Usage:
        @traced("webhook.dispatch")
        def dispatch_webhook(delivery):
            ...

        @traced(attributes={"component": "sla-calculator"})
        def calculate_sla(outage):
            ...
    """

    def decorator(func: Callable) -> Callable:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name, attributes=attributes or {}) as span:
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                    raise

        return wrapper

    return decorator
