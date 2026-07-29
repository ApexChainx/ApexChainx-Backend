"""Token revocation service backed by Redis.

Stores revoked token hashes in Redis with TTL matching the token's remaining lifetime.
"""

from typing import Optional
from redis import Redis

from app.core.config import settings

_revocation_redis: Redis | None = None


def _get_redis() -> Redis:
    global _revocation_redis
    if _revocation_redis is None:
        _revocation_redis = Redis.from_url(settings.CELERY_BROKER_URL)
    return _revocation_redis


def revoke(token_hash: str, ttl_seconds: int) -> None:
    """Store a token hash in the revocation list with the given TTL."""
    key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{token_hash}"
    _get_redis().setex(key, ttl_seconds, "1")


def is_revoked(token_hash: str) -> bool:
    """Check if a token hash has been revoked."""
    key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{token_hash}"
    return _get_redis().exists(key) > 0
