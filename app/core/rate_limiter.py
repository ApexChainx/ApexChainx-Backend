"""Auth rate limiting with Redis and persistent database enforcement."""

import hashlib
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from time import time
from typing import Any, ClassVar

import redis
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.auth_attempt_ledger import record_rate_limit_attempt
from app.services.metrics import increment_counter, set_gauge

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
-- Expiry is re-armed on every scored request, so a key lives at most `window`
-- past the last hit and then removes itself (#544). `window` needs no safety
-- margin: the ZREMRANGEBYSCORE above can never keep a member that is older than
-- the window, so anything older is already useless, and a margin would only
-- extend the key's life.
redis.call('EXPIRE', key, window)
return 1
"""

# Once the in-process map holds more keys than this, `SimpleRateLimiter` sweeps
# expired keys on the next request instead of waiting for each key to be
# presented again. Sweeping is O(n), so it runs only when the map has actually
# grown, and every request still prunes its own key first.
SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD = 1024


@dataclass(frozen=True)
class RateLimitPolicy:
    """How many requests a key may make within a sliding window."""

    limit: int
    window_seconds: int


# Scopes are the stable prefixes callers put in front of the client identifier
# (``login_ip_1.2.3.4`` -> ``login_ip``). They select a per-scope policy and are
# the only part of a key that reaches metrics, so client IPs and user ids never
# become label values.
KNOWN_SCOPES: tuple[str, ...] = (
    "register_ip",
    "login_ip",
    "refresh_ip",
    "profile_update",
    "impersonate",
)
UNKNOWN_SCOPE = "other"

# Per-scope overrides. A scope missing here uses the global
# AUTH_RATE_LIMIT_REQUESTS / AUTH_RATE_LIMIT_WINDOW_SECONDS settings.
RATE_LIMIT_POLICIES: dict[str, RateLimitPolicy] = {}


def rate_limit_scope(key: str) -> str:
    for scope in KNOWN_SCOPES:
        if key.startswith(f"{scope}_"):
            return scope
    return UNKNOWN_SCOPE


def default_policy() -> RateLimitPolicy:
    return RateLimitPolicy(settings.AUTH_RATE_LIMIT_REQUESTS, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)


def resolve_policy(key: str, policy: RateLimitPolicy | None = None) -> RateLimitPolicy:
    """Pick the policy for ``key``: an explicit one, then its scope's, then the default."""
    if policy is not None:
        return policy
    return RATE_LIMIT_POLICIES.get(rate_limit_scope(key)) or default_policy()


def _record_decision(backend: str, key: str, allowed: bool) -> bool:
    increment_counter(
        "rate_limiter_requests_total",
        tags={"backend": backend, "scope": rate_limit_scope(key), "outcome": "allowed" if allowed else "rejected"},
    )
    return allowed


def _record_ledger_attempt(db: Session, key: str, policy: RateLimitPolicy) -> bool:
    allowed = record_rate_limit_attempt(db, key, policy.limit, policy.window_seconds)
    return _record_decision("ledger", key, allowed)


class SimpleRateLimiter:
    _shared: ClassVar[dict[str, list[float]]] = defaultdict(list)

    def __init__(self) -> None:
        self.requests = SimpleRateLimiter._shared

    def is_allowed(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> bool:
        """Check the persistent ledger when an authentication session is available."""
        policy = resolve_policy(key, policy)
        if db is not None:
            return _record_ledger_attempt(db, key, policy)

        now = time()
        window_start = now - policy.window_seconds
        self.requests[key] = [t for t in self.requests[key] if t > window_start]
        if len(self.requests) > SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD:
            self.cull_expired(now=now)
        if len(self.requests[key]) >= policy.limit:
            return _record_decision("memory", key, False)

        self.requests[key].append(now)
        return _record_decision("memory", key, True)

    def cull_expired(self, now: float | None = None) -> int:
        """Drop keys whose every recorded hit is older than the window.

        `is_allowed` only prunes the key it was handed, so a client that stops
        appearing — an admin key used once, a scanner that rotates addresses —
        kept its list in `_shared` for the lifetime of the process. Nothing else
        reclaimed it: `_shared` is class-level state, so the entries outlive both
        the limiter instance and the request (#544).

        Returns:
            Number of keys removed.
        """
        current = now if now is not None else time()
        expired = [
            key
            for key, hits in self.requests.items()
            if not hits or hits[-1] <= current - resolve_policy(key).window_seconds
        ]
        for key in expired:
            del self.requests[key]
        if expired:
            increment_counter("rate_limiter_keys_culled_total", value=len(expired))
        set_gauge("rate_limiter_memory_keys", len(self.requests))
        return len(expired)


class RedisRateLimiter:
    def __init__(self) -> None:
        self.fallback = _shared_fallback
        self.disabled_until: float | None = None
        # Shared, lazily-connected clients (sync for sync callers, async for
        # async callers). No event loop is created per request.
        self.client = redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        self.async_client = redis_async.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)

    def _key_namespace(self, key: str) -> str:
        bounded_key = key if len(key) <= 128 else hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"auth_rate_limiter:{bounded_key}"

    def _is_circuit_open(self) -> bool:
        return self.disabled_until is not None and time() < self.disabled_until

    def _trip_circuit(self, exc: Exception) -> None:
        reason = "redis_error" if isinstance(exc, (RedisError, OSError, RuntimeError)) else "unexpected_error"
        increment_counter("rate_limiter_fallback_total", tags={"reason": reason})
        set_gauge("rate_limiter_circuit_open", 1)
        self.disabled_until = time() + 30

    def _lua_args(self, key: str, policy: RateLimitPolicy) -> tuple[Any, ...]:
        if not settings.CELERY_BROKER_URL.strip():
            raise RedisError("CELERY_BROKER_URL is empty")
        encoded_key = self._key_namespace(key)
        now_ts = int(time())
        member = f"{now_ts}-{random.random()}"  # nosec B311 - unique sorted-set member, not security
        return (encoded_key, now_ts, policy.window_seconds, policy.limit, member)

    def _eval(self, key: str, policy: RateLimitPolicy) -> bool:
        result = self.client.eval(RATE_LIMITER_LUA, 1, *self._lua_args(key, policy))
        return _record_decision("redis", key, bool(result))

    async def _eval_async(self, key: str, policy: RateLimitPolicy) -> bool:
        result = await self.async_client.eval(RATE_LIMITER_LUA, 1, *self._lua_args(key, policy))
        return _record_decision("redis", key, bool(result))

    def _redis_usable(self) -> bool:
        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return False
        if self._is_circuit_open():
            return False
        set_gauge("rate_limiter_circuit_open", 0)
        return True

    def is_allowed(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> bool:
        policy = resolve_policy(key, policy)
        if db is not None:
            if not _record_ledger_attempt(db, key, policy):
                return False
            if not self._redis_usable():
                return True

            try:
                return self._eval(key, policy)
            except Exception as exc:
                logger.warning("Redis rate limiter unavailable; using persistent attempt ledger: %s", exc)
                self._trip_circuit(exc)
                return True

        if not self._redis_usable():
            return self.fallback.is_allowed(key, policy=policy)

        try:
            return self._eval(key, policy)
        except Exception as exc:
            logger.warning("Redis rate limiter unavailable, falling back to in-memory limiter: %s", exc)
            self._trip_circuit(exc)
            return self.fallback.is_allowed(key, policy=policy)

    async def is_allowed_async(self, key: str, policy: RateLimitPolicy | None = None) -> bool:
        policy = resolve_policy(key, policy)
        if not self._redis_usable():
            return self.fallback.is_allowed(key, policy=policy)

        try:
            return await self._eval_async(key, policy)
        except Exception as exc:
            logger.warning("Redis rate limiter unavailable, falling back to in-memory limiter: %s", exc)
            self._trip_circuit(exc)
            return self.fallback.is_allowed(key, policy=policy)


# OpenAPI description of the 429 every rate-limited endpoint can return. The
# body is the RFC 7807 problem document produced by app.api.exception_handlers.
RATE_LIMITED_RESPONSE: dict[int | str, dict[str, Any]] = {
    429: {
        "description": (
            "Rate limit exceeded. The caller made more requests than its scope allows "
            "within the sliding window; retry after the window has passed."
        ),
        "content": {
            "application/problem+json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string"},
                        "status": {"type": "integer", "example": 429},
                        "detail": {"type": "string"},
                        "error_code": {"type": "string", "example": "rate_limited"},
                    },
                },
                "example": {
                    "type": "about:blank",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": "Too many login attempts from this IP. Please try again later.",
                    "error_code": "rate_limited",
                },
            }
        },
    }
}


def with_rate_limit_responses(responses: dict[int | str, dict[str, Any]] | None = None) -> dict[int | str, dict[str, Any]]:
    """Merge the documented 429 into an endpoint's ``responses``, keeping its own description."""
    merged: dict[int | str, dict[str, Any]] = {code: dict(spec) for code, spec in (responses or {}).items()}
    merged[429] = {**RATE_LIMITED_RESPONSE[429], **merged.get(429, {})}
    return merged


# Shared fallback for RedisRateLimiter instances so that in-memory rate-limiting
# state is consistent across all instances when Redis is unavailable.
_shared_fallback = SimpleRateLimiter()

rate_limiter = (
    RedisRateLimiter()
    if settings.USE_REDIS_RATE_LIMITER and not settings.CELERY_TASK_ALWAYS_EAGER
    else SimpleRateLimiter()
)
