"""
Auth rate limiter implementation.

This module provides a Redis-backed sliding-window rate limiter with a
fallback to an in-process token bucket when Redis is unavailable or when
`USE_REDIS_RATE_LIMITER` is disabled.
"""

import asyncio
import logging
import random
from collections import defaultdict
from time import time
from typing import Dict, List

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

RATE_LIMITER_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local window_start = now - window
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return 1
"""


class SimpleRateLimiter:
    _shared: Dict[str, List[float]] = defaultdict(list)

    def __init__(self) -> None:
        self.requests = SimpleRateLimiter._shared

    def is_allowed(self, key: str) -> bool:
        """Check if the key is allowed based on rate limits."""
        now = time()
        window_start = now - settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

        self.requests[key] = [t for t in self.requests[key] if t > window_start]
        if len(self.requests[key]) >= settings.AUTH_RATE_LIMIT_REQUESTS:
            return False

        self.requests[key].append(now)
        return True


class RedisRateLimiter:
    def __init__(self) -> None:
        self.fallback = _shared_fallback
        self.disabled_until: float | None = None
        self.client = redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)

    def _key_namespace(self, key: str) -> str:
        return f"auth_rate_limiter:{key}"

    def _is_circuit_open(self) -> bool:
        return self.disabled_until is not None and time() < self.disabled_until

    def _trip_circuit(self) -> None:
        self.disabled_until = time() + 30

    async def _is_allowed_async(self, key: str) -> bool:
        if not settings.CELERY_BROKER_URL.strip():
            raise RedisError("CELERY_BROKER_URL is empty")

        encoded_key = self._key_namespace(key)
        now_ts = int(time())
        member = f"{now_ts}-{random.random()}"
        result = await self.client.eval(
            RATE_LIMITER_LUA,
            1,
            encoded_key,
            now_ts,
            settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
            settings.AUTH_RATE_LIMIT_REQUESTS,
            member,
        )
        return bool(result)

    def _run_coroutine(self, coro):
        try:
            return asyncio.run(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    def is_allowed(self, key: str) -> bool:
        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return self.fallback.is_allowed(key)

        if self._is_circuit_open():
            return self.fallback.is_allowed(key)

        try:
            return self._run_coroutine(self._is_allowed_async(key))
        except (RedisError, OSError, RuntimeError) as exc:
            logger.warning(
                "Redis rate limiter unavailable, falling back to in-memory limiter: %s",
                exc,
            )
            self._trip_circuit()
            return self.fallback.is_allowed(key)
        except Exception as exc:
            logger.warning(
                "Unexpected rate limiter error, falling back to in-memory limiter: %s",
                exc,
            )
            self._trip_circuit()
            return self.fallback.is_allowed(key)


# Shared fallback for RedisRateLimiter instances so that in-memory rate-limiting
# state is consistent across all instances when Redis is unavailable.
_shared_fallback = SimpleRateLimiter()

rate_limiter = (
    RedisRateLimiter()
    if settings.USE_REDIS_RATE_LIMITER and not settings.CELERY_TASK_ALWAYS_EAGER
    else SimpleRateLimiter()
)
