"""Idempotency middleware for state-changing endpoints.

Reads the Idempotency-Key header on POST/PUT/PATCH/DELETE requests, caches
responses in Redis, and replays them on duplicate requests with the same key.
"""
import hashlib
import json
from typing import Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from redis import Redis
from app.core.config import settings


def _compute_fingerprint(method: str, path: str, body: bytes) -> str:
    canonical = json.dumps(json.loads(body if body else b"{}"), sort_keys=True) if body else "{}"
    raw = f"{method}:{path}:{canonical}".encode()
    return hashlib.sha256(raw).hexdigest()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: Optional[Redis] = None):
        super().__init__(app)
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)
        self.ttl = settings.IDEMPOTENCY_KEY_TTL_HOURS * 3600

    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        body = await request.body()
        fingerprint = _compute_fingerprint(request.method, request.url.path, body)
        cache_key = f"idempotency:{idempotency_key}"

        existing = self.redis.get(cache_key)
        if existing is not None:
            cached = json.loads(existing)
            if cached["fingerprint"] != fingerprint:
                return Response(
                    status_code=409,
                    content=json.dumps({"detail": "Idempotency-Key already used with different payload"}),
                    media_type="application/json",
                )
            return Response(
                status_code=cached["status_code"],
                content=cached["body"],
                media_type=cached.get("media_type", "application/json"),
                headers={"Content-Location": f"{request.url.path}"},
            )

        response = await call_next(request)
        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        cached_response = {
            "fingerprint": fingerprint,
            "status_code": response.status_code,
            "body": response_body.decode(),
            "media_type": response.media_type or "application/json",
        }
        self.redis.setex(cache_key, self.ttl, json.dumps(cached_response))

        return Response(
            status_code=response.status_code,
            content=response_body,
            media_type=response.media_type,
            headers=dict(response.headers),
        )