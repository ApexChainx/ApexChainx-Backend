from datetime import datetime, timezone
from fastapi import FastAPI, Request
issue/114-117-webhook-concurrency-canonical-json
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse, RedirectResponse
main
from redis import ConnectionError, Redis, TimeoutError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware, SAFELISTED_HEADERS, ALL_METHODS
from starlette.types import ASGIApp, Receive, Scope, Send

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

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
)
from app.core.logging_config import configure_logging
from app.core.lifecycle import install_signal_handlers
from app.core.tracing import init_tracing, instrument_app
from app.core.exceptions import integrity_error_handler, pydantic_validation_handler
from app.db.session import engine
from app.services.health_report import build_readiness_report
from app.middleware.content_type import ContentTypeMiddleware
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.etag import ETagMiddleware
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.api_version import ApiVersionMiddleware


configure_logging()
validate_critical_settings(settings)
install_signal_handlers()
init_tracing()


async def check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        return True
    except ConnectionError:
        return False


async def check_celery() -> bool:
    try:
        r = Redis.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        return True
    except (ConnectionError, TimeoutError):
        return False


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, description="ApexChainx Backend API")

app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(ValidationError, pydantic_validation_handler)
app.add_exception_handler(RequestValidationError, pydantic_validation_handler)
# Content-type negotiation middleware (before correlation to catch early)
app.add_middleware(ContentTypeMiddleware)

# Add correlation middleware first (before CORS to ensure it runs on all requests)
app.add_middleware(CorrelationMiddleware)

# Add idempotency middleware (after correlation)
app.add_middleware(IdempotencyMiddleware)


class _DynamicCORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        allow_origins = settings.ALLOWED_ORIGINS
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
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "detail": exc.detail,
            **(exc.extra or {}),
        },
    )


@app.exception_handler(ApexNotFoundError)
async def apex_not_found_handler(request: Request, exc: ApexNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error_code": "not_found", "detail": exc.detail},
    )


@app.exception_handler(ApexTransientError)
async def apex_transient_error_handler(request: Request, exc: ApexTransientError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "transient_error",
            "detail": exc.detail,
            "retryable": True,
        },
    )


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/health/readiness")
async def readiness():
    report = build_readiness_report(engine, settings.CELERY_BROKER_URL)
    report["timestamp"] = datetime.now(timezone.utc).isoformat()
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
