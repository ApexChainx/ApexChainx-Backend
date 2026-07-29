from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from datetime import datetime
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from redis import Redis

from app.api.v1.router import api_router
from app.core.config import settings, validate_critical_settings
from app.core.logging_config import configure_logging
from app.core.lifecycle import install_signal_handlers
from app.core.exceptions import integrity_error_handler, pydantic_validation_handler
from app.db.session import engine
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.payload_size import PayloadSizeMiddleware
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware


configure_logging()
validate_critical_settings(settings)
install_signal_handlers()

async def check_database() -> bool:
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        return True
    except Exception:
        return False

async def check_celery() -> bool:
    from redis import Redis
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

app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(ValidationError, pydantic_validation_handler)

# Add correlation middleware first (before CORS to ensure it runs on all requests)
app.add_middleware(CorrelationMiddleware)

# Add payload size middleware (after correlation, before CORS)
app.add_middleware(PayloadSizeMiddleware)

# Add idempotency middleware (after payload size)
app.add_middleware(IdempotencyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.CORS_ALLOWED_METHODS,
    allow_headers=settings.CORS_ALLOWED_HEADERS,
    expose_headers=settings.CORS_EXPOSE_HEADERS,
)

# Security headers should be applied after CORS so preflight responses are handled
app.add_middleware(SecurityHeadersMiddleware)


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/health/readiness")
async def readiness():
    report = build_readiness_report(engine, settings.CELERY_BROKER_URL)
    report["timestamp"] = datetime.now(timezone.utc).isoformat()
    return report

# Legacy health check (now liveness)
@app.get("/health")
def health_check():
    return {"status": "ok"}

# API routes
app.include_router(api_router, prefix="/api/v1")
