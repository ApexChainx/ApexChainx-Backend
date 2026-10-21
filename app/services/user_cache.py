"""Redis read-through cache for hot User API lookups (#676).

Mirrors ``WalletCache``/``SLACache``: a thin helper around a Redis client that
stores JSON blobs under a namespaced key with a short TTL.  Cache failures are
logged and treated as misses so a degraded Redis never breaks the request path.
"""

from __future__ import annotations

import json
import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30


class UserCache:
    """Read-through cache for per-user session inventories."""

    def __init__(self, redis_client: Redis, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._ttl = ttl

    @staticmethod
    def _key(email: str) -> str:
        return f"user:sessions:{email}"

    def get_sessions(self, email: str) -> list | None:
        try:
            raw = self._redis.get(self._key(email))
        except RedisError as exc:
            logger.warning("User cache read failed for %s: %s", email, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Corrupt JSON in user cache for %s, treating as miss", email)
            return None

    def set_sessions(self, email: str, sessions: list) -> None:
        try:
            # ``default=str`` keeps datetime timestamps JSON-serialisable; the
            # consumer (SessionInfo) parses the ISO strings back.
            self._redis.setex(self._key(email), self._ttl, json.dumps(sessions, default=str))
        except RedisError as exc:
            logger.warning("User cache write failed for %s: %s", email, exc)

    def invalidate(self, email: str) -> None:
        try:
            self._redis.delete(self._key(email))
        except RedisError as exc:
            logger.warning("User cache invalidate failed for %s: %s", email, exc)
