"""Redis read-through cache for hot Auth API lookups (#660).

Mirrors the shape of ``WalletCache``/``SLACache``: a thin helper around a Redis
client that stores JSON blobs under a namespaced key with a short TTL.  Cache
failures are logged and treated as misses so a degraded Redis never breaks the
authentication path.
"""

from __future__ import annotations

import json
import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 60


class AuthCache:
    """Read-through cache for authenticated user lookups."""

    def __init__(self, redis_client: Redis, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._ttl = ttl

    @staticmethod
    def _key(email: str) -> str:
        return f"auth:user:{email}"

    def get_user(self, email: str) -> dict | None:
        try:
            raw = self._redis.get(self._key(email))
        except RedisError as exc:
            logger.warning("Auth cache read failed for %s: %s", email, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Corrupt JSON in auth cache for %s, treating as miss", email)
            return None

    def set_user(self, email: str, data: dict) -> None:
        try:
            self._redis.setex(self._key(email), self._ttl, json.dumps(data))
        except RedisError as exc:
            logger.warning("Auth cache write failed for %s: %s", email, exc)

    def invalidate_user(self, email: str) -> None:
        try:
            self._redis.delete(self._key(email))
        except RedisError as exc:
            logger.warning("Auth cache invalidate failed for %s: %s", email, exc)
