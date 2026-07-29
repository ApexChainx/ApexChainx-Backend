from fastapi import FastAPI
from datetime import datetime
from sqlalchemy import text
from redis import Redis
from starlette.middleware.cors import CORSMiddleware, SAFELISTED_HEADERS, ALL_METHODS
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.v1.router import api_router
from app.core.config import settings, validate_critical_settings
from app.db.session import engine
from app.middleware.content_type import ContentTypeMiddleware
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.payload_size import PayloadSizeMiddleware
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware

validate_critical_settings(settings)

async def check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        return True
    except Exception:
        return False

async def check_celery() -> bool:
    try:
        r = Redis.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        return True
    except Exception:
        return False

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="ApexChainx Backend API"
)

# Content-type negotiation middleware (before correlation to catch early)
app.add_middleware(ContentTypeMiddleware)

# Add correlation middleware first (before CORS to ensure it runs on all requests)
app.add_middleware(CorrelationMiddleware)

# Add payload size middleware (after correlation, before CORS)
app.add_middleware(PayloadSizeMiddleware)

# Add idempotency middleware (after payload size)
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
        preflight_headers.update({
            "Access-Control-Allow-Methods": ", ".join(allow_methods),
            "Access-Control-Max-Age": str(600),
        })
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


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/health/readiness")
async def readiness():
    db_ok = await check_database()
    celery_ok = await check_celery()
    status = "ok" if db_ok and celery_ok else "degraded"
    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": "ok" if db_ok else "down",
            "celery": "ok" if celery_ok else "down",
        }
    }

# Legacy health check (now liveness)
@app.get("/health")
def health_check():
    return {"status": "ok"}

# API routes
app.include_router(api_router, prefix="/api/v1")
