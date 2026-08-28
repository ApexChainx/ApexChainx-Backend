import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import ALL_METHODS, SAFELISTED_HEADERS, CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.exception_handlers import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.api.v1.router import api_router
from app.core.config import settings, validate_critical_settings
from app.core.exceptions import (
    ApexException,
    ApexNotFoundError,
    ApexTransientError,
    integrity_error_handler,
    pydantic_validation_handler,
)
from app.core.lifecycle import install_signal_handlers
from app.core.logging_config import configure_logging
from app.core.tracing import init_tracing, instrument_app
from app.db.session import audit_engine, engine
from app.middleware.api_version import ApiVersionMiddleware
from app.middleware.content_type import ContentTypeMiddleware
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.etag import ETagMiddleware
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.health_report import build_readiness_report
from app.utils.correlation_ctx import get_or_generate_correlation_id

configure_logging()
validate_critical_settings(settings)
install_signal_handlers()
init_tracing()


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, description="ApexChainx Backend API")

app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(ValidationError, pydantic_validation_handler)
app.add_exception_handler(RequestValidationError, pydantic_validation_handler)
# Content-type negotiation middleware (before correlation to catch early)
app.add_middleware(ContentTypeMiddleware)
app.add_middleware(ETagMiddleware)

# Add correlation middleware first (before CORS to ensure it runs on all requests)
app.add_middleware(CorrelationMiddleware)

# Add idempotency middleware (after correlation)
app.add_middleware(IdempotencyMiddleware)


class _DynamicCORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._origins_cache = list(settings.ALLOWED_ORIGINS)

    def refresh_origins(self) -> None:
        """Re-read allowed origins from settings."""
        self._origins_cache = list(settings.ALLOWED_ORIGINS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        allow_origins = self._origins_cache
        allow_methods = settings.CORS_ALLOWED_METHODS
        allow_headers = settings.CORS_ALLOWED_HEADERS
        expose_headers = settings.CORS_EXPOSE_HEADERS
        allow_credentials = True

        if "*" in allow_methods:
            allow_methods = ALL_METHODS

        allow_all_origins = "*" in allow_origins
        allow_all_headers = "*" in allow_headers
        preflight_explicit_allow_origin = not allow_all_origins or allow_credentials

        simple_headers: dict[str, str] = {}
        if allow_all_origins:
            simple_headers["Access-Control-Allow-Origin"] = "*"
        if allow_credentials:
            simple_headers["Access-Control-Allow-Credentials"] = "true"
        if expose_headers:
            simple_headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)

        preflight_headers: dict[str, str] = {}
        if preflight_explicit_allow_origin:
            preflight_headers["Vary"] = "Origin"
        else:
            preflight_headers["Access-Control-Allow-Origin"] = "*"
        preflight_headers.update(
            {
                "Access-Control-Allow-Methods": ", ".join(allow_methods),
                "Access-Control-Max-Age": str(600),
            }
        )
        merged_headers = sorted(SAFELISTED_HEADERS | set(allow_headers))
        if merged_headers and not allow_all_headers:
            preflight_headers["Access-Control-Allow-Headers"] = ", ".join(merged_headers)
        if allow_credentials:
            preflight_headers["Access-Control-Allow-Credentials"] = "true"

        self.allow_origins = allow_origins
        self.allow_methods = allow_methods
        self.allow_headers = [h.lower() for h in merged_headers]
        self.allow_all_origins = allow_all_origins
        self.allow_all_headers = allow_all_headers
        self.allow_credentials = allow_credentials
        self.preflight_explicit_allow_origin = preflight_explicit_allow_origin
        self.allow_origin_regex = None
        self.allow_private_network = False
        self.simple_headers = simple_headers
        self.preflight_headers = preflight_headers

        await CORSMiddleware.__call__(self, scope, receive, send)


app.add_middleware(_DynamicCORSMiddleware)

# Security headers should be applied after CORS so preflight responses are handled
app.add_middleware(SecurityHeadersMiddleware)

# API version and commit headers on every response
app.add_middleware(ApiVersionMiddleware)


@app.exception_handler(ApexException)
async def apex_exception_handler(request: Request, exc: ApexException) -> JSONResponse:
    correlation_id = get_or_generate_correlation_id()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": f"https://developer.apexchainx.io/errors/{exc.status_code}",
            "title": getattr(exc, "error_code", "Domain Error"),
            "status": exc.status_code,
            "detail": exc.detail,
            "correlation_id": correlation_id,
            "error_code": getattr(exc, "error_code", "domain_error"),
            **(getattr(exc, "extra", None) or {}),
        },
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


@app.exception_handler(ApexNotFoundError)
async def apex_not_found_handler(request: Request, exc: ApexNotFoundError) -> JSONResponse:
    correlation_id = get_or_generate_correlation_id()
    return JSONResponse(
        status_code=404,
        content={
            "type": "https://developer.apexchainx.io/errors/404",
            "title": "Not Found",
            "status": 404,
            "detail": exc.detail,
            "correlation_id": correlation_id,
            "error_code": "not_found",
        },
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


@app.exception_handler(ApexTransientError)
async def apex_transient_error_handler(request: Request, exc: ApexTransientError) -> JSONResponse:
    correlation_id = get_or_generate_correlation_id()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": f"https://developer.apexchainx.io/errors/{exc.status_code}",
            "title": "Service Unavailable",
            "status": exc.status_code,
            "detail": exc.detail,
            "correlation_id": correlation_id,
            "error_code": "transient_error",
            "retryable": True,
        },
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/health/readiness")
async def readiness():
    # Run the (blocking) probe work on a worker thread so a slow DB/Redis never
    # stalls the event loop.
    report = await asyncio.to_thread(
        build_readiness_report, engine, audit_engine, settings.CELERY_BROKER_URL
    )
    report["timestamp"] = datetime.now(UTC).isoformat()
    return report


# Legacy health check – deprecated, redirects to /health/liveness
@app.get("/health", include_in_schema=False)
def health_check():
    return RedirectResponse(
        url="/health/liveness",
        status_code=308,
        headers={"Deprecation": "true"},
    )


# Register RFC 7807 exception handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


# API routes
app.include_router(api_router, prefix="/api/v1")

# Apply OpenTelemetry auto-instrumentation (after all routes registered)
instrument_app(app)
