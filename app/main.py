from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from datetime import datetime
from sqlalchemy import text
from redis import Redis

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
