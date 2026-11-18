"""Auth rate limiting with Redis and persistent database enforcement.

Error handling
--------------
* :class:`RateLimitError` is raised by ``check_or_raise`` when a key is over
  its limit, so callers can answer HTTP 429 without re-checking the key.
* Transient Redis failures are retried with exponential backoff before the
  circuit is tripped; while the circuit is open the limiter degrades to the
  in-memory or persistent-ledger path instead of hammering a failing Redis.
* Every retry and fallback logs a structured record (``rate_limiter_key``,
  ``backend``, ``error_type``, ``attempt``) so alerting can key off it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from time import sleep as _time_sleep
from time import time
from typing import ClassVar

import redis
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import metrics
from app.services.auth_attempt_ledger import record_rate_limit_attempt

logger = logging.getLogger(__name__)

EMPTY_RATE_LIMIT_KEY = "anonymous"
RATE_LIMIT_KEY_MAX_LENGTH = 128
REDIS_DENY_CACHE_SECONDS = 2
REDIS_DENY_CACHE_MAX_KEYS = 1024
UNKNOWN_SCOPE = "unknown"

#: Redis attempts per check before the circuit is tripped.
REDIS_MAX_ATTEMPTS: int = 3

#: Delay before the first retry, doubled for each subsequent attempt.
REDIS_BASE_DELAY_SECONDS: float = 0.05

#: Upper bound for a single retry delay.
REDIS_MAX_DELAY_SECONDS: float = 0.5

#: How long the Redis circuit stays open once it has been tripped.
CIRCUIT_RESET_SECONDS: float = 30.0

#: Errors that indicate a transient Redis problem worth retrying.  A
#: ``RuntimeError`` from event-loop handling is deliberately excluded: it is
#: deterministic, so retrying it would only add latency.
RETRYABLE_REDIS_ERRORS: tuple[type[BaseException], ...] = (RedisError, OSError, TimeoutError)

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

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9:._-]")
_SCOPE_PREFIXES = (
    "login_ip",
    "register_ip",
    "refresh_ip",
    "profile_update",
    "impersonate",
)


@dataclass(frozen=True)
class RateLimitPolicy:
    """Per-scope rate-limit configuration."""

    limit: int
    window_seconds: int


RATE_LIMIT_POLICIES: dict[str, RateLimitPolicy] = {}
RATE_LIMITED_RESPONSE = {
    429: {
        "description": "Rate limit exceeded",
        "content": {
            "application/problem+json": {
                "example": {
                    "type": "about:blank",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": "Rate limit exceeded",
                    "error_code": "rate_limited",
                }
            }
        },
    }
}


def sanitize_rate_limit_key(key: object) -> str:
    """Return a bounded, control-free key suitable for Redis/in-memory maps."""
    if key is None:
        raw = ""
    else:
        raw = str(key)
    normalized = unicodedata.normalize("NFKC", raw).strip()
    if not normalized:
        return EMPTY_RATE_LIMIT_KEY
    sanitized = "".join("_" if unicodedata.category(ch).startswith("C") else ch for ch in normalized)
    sanitized = _SAFE_KEY_RE.sub("_", sanitized)
    if not sanitized:
        return EMPTY_RATE_LIMIT_KEY
    if len(sanitized) > RATE_LIMIT_KEY_MAX_LENGTH:
        return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    return sanitized


def rate_limit_scope(key: str) -> str:
    sanitized = sanitize_rate_limit_key(key)
    for prefix in _SCOPE_PREFIXES:
        if sanitized.startswith(prefix):
            return prefix
    return UNKNOWN_SCOPE


def resolve_policy(key: str, policy: RateLimitPolicy | None = None) -> RateLimitPolicy:
    if policy is not None:
        return policy
    scope = rate_limit_scope(key)
    scoped = RATE_LIMIT_POLICIES.get(scope)
    if scoped is not None:
        return scoped
    return RateLimitPolicy(
        limit=int(settings.AUTH_RATE_LIMIT_REQUESTS),
        window_seconds=int(settings.AUTH_RATE_LIMIT_WINDOW_SECONDS),
    )


class RateLimitError(Exception):
    """Raised when a key has exceeded its rate limit.

    Attributes:
        key: The rate-limiter key that was rejected (for example
            ``"login_ip_203.0.113.7"``).
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Rate limit exceeded for key {key!r}")


def _backoff_delay(attempt: int) -> float:
    """Return the delay in seconds to wait after the *attempt*-th failure.

    Args:
        attempt: 1-based number of the attempt that just failed.

    Returns:
        ``min(REDIS_BASE_DELAY_SECONDS * 2 ** (attempt - 1),
        REDIS_MAX_DELAY_SECONDS)``.
    """
    return min(REDIS_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1)), REDIS_MAX_DELAY_SECONDS)


class SimpleRateLimiter:
    _shared: ClassVar[dict[str, list[float]]] = defaultdict(list)

    def __init__(self) -> None:
        self.requests = SimpleRateLimiter._shared

    def _prune(self, key: str, *, window_seconds: int, now: float) -> list[float]:
        window_start = now - window_seconds
        hits = [t for t in self.requests.get(key, []) if t > window_start]
        if hits:
            self.requests[key] = hits
        elif key in self.requests:
            del self.requests[key]
        return hits

    def is_allowed(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> bool:
        """Check the persistent ledger when an authentication session is available."""
        raw_key = key
        safe_key = sanitize_rate_limit_key(raw_key)
        resolved = resolve_policy(safe_key, policy)
        scope = rate_limit_scope(safe_key)
        if db is not None:
            allowed = record_rate_limit_attempt(db, safe_key, resolved.limit, resolved.window_seconds)
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "ledger", "scope": scope, "outcome": "allowed" if allowed else "rejected"},
            )
            return allowed

        now = time()
        hits = self._prune(safe_key, window_seconds=resolved.window_seconds, now=now)
        if len(self.requests) + 1 > SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD:
            self.cull_expired(now=now)
            hits = self._prune(safe_key, window_seconds=resolved.window_seconds, now=now)
        if len(hits) >= resolved.limit:
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "memory", "scope": scope, "outcome": "rejected"},
            )
            return False

        self.requests[safe_key] = [*hits, now]
        metrics.increment_counter(
            "rate_limiter_requests_total",
            tags={"backend": "memory", "scope": scope, "outcome": "allowed"},
        )
        metrics.set_gauge("rate_limiter_memory_keys", float(len(self.requests)))
        return True

    def cull_expired(self, now: float | None = None) -> int:
        """Drop keys whose every recorded hit is older than their scope window."""
        current = now if now is not None else time()
        expired: list[str] = []
        for key, hits in list(self.requests.items()):
            resolved = resolve_policy(key)
            window_start = current - resolved.window_seconds
            fresh = [t for t in hits if t > window_start]
            if fresh:
                self.requests[key] = fresh
            else:
                expired.append(key)
        for key in expired:
            self.requests.pop(key, None)
        if expired:
            metrics.increment_counter("rate_limiter_keys_culled_total", len(expired))
        metrics.set_gauge("rate_limiter_memory_keys", float(len(self.requests)))
        return len(expired)

    def check_or_raise(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> None:
        """Check the rate limit and raise :class:`RateLimitError` if exceeded."""
        if self.is_allowed(key, db=db, policy=policy):
            return

        logger.warning(
            "Rate limit exceeded",
            extra={
                "rate_limiter_key": sanitize_rate_limit_key(key),
                "backend": "ledger" if db is not None else "memory",
                "circuit_open": False,
            },
        )
        raise RateLimitError(sanitize_rate_limit_key(key))


class RedisRateLimiter:
    def __init__(self, sleep: Callable[[float], None] | None = None) -> None:
        """Create the limiter.

        Args:
            sleep: Callable used to wait between Redis retries in the sync
                path.  Injected so tests can record backoff delays without
                sleeping.  Defaults to :func:`time.sleep`.
        """
        self.fallback = _shared_fallback
        self.disabled_until: float | None = None
        # Shared, lazily-connected clients (sync for sync callers, async for
        # async callers). No event loop is created per request.
        self.client = redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        self.async_client = redis_async.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        self._sleep = sleep or _time_sleep
        self.deny_cache: dict[str, float] = {}
        self._deny_cache_ttl_seconds = REDIS_DENY_CACHE_SECONDS

    def _key_namespace(self, key: str) -> str:
        return f"auth_rate_limiter:{sanitize_rate_limit_key(key)}"

    def _deny_cache_ttl(self) -> int:
        return min(
            int(getattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", REDIS_DENY_CACHE_SECONDS)),
            int(self._deny_cache_ttl_seconds),
        )

    def clear_cache(self) -> None:
        self.deny_cache.clear()

    def _cached_denied(self, key: str) -> bool:
        expires_at = self.deny_cache.get(key)
        if expires_at is None:
            return False
        if expires_at <= time():
            self.deny_cache.pop(key, None)
            return False
        return True

    def _cache_denial(self, key: str) -> None:
        ttl = self._deny_cache_ttl()
        if ttl <= 0:
            return
        self.deny_cache[key] = time() + ttl
        while len(self.deny_cache) > REDIS_DENY_CACHE_MAX_KEYS:
            oldest = min(self.deny_cache, key=self.deny_cache.get)
            self.deny_cache.pop(oldest, None)

    def _is_circuit_open(self) -> bool:
        return self.disabled_until is not None and time() < self.disabled_until

    def _trip_circuit(self) -> None:
        self.disabled_until = time() + CIRCUIT_RESET_SECONDS
        metrics.set_gauge("rate_limiter_circuit_open", 1.0)

    def _reset_circuit(self) -> None:
        if self.disabled_until is not None and time() >= self.disabled_until:
            self.disabled_until = None
            metrics.set_gauge("rate_limiter_circuit_open", 0.0)

    def _lua_args(self, key: str, policy: RateLimitPolicy) -> tuple[str, int, int, int, str]:
        if not settings.CELERY_BROKER_URL.strip():
            raise RedisError("CELERY_BROKER_URL is empty")
        encoded_key = self._key_namespace(key)
        now_ts = int(time())
        member = f"{now_ts}-{random.random()}"  # nosec B311 - unique sorted-set member, not security
        return (
            encoded_key,
            now_ts,
            policy.window_seconds,
            policy.limit,
            member,
        )

    def _eval(self, key: str, policy: RateLimitPolicy) -> bool:
        encoded_key, now_ts, window, limit, member = self._lua_args(key, policy)
        result = self.client.eval(RATE_LIMITER_LUA, 1, encoded_key, now_ts, window, limit, member)
        return bool(result)

    async def _eval_async(self, key: str, policy: RateLimitPolicy) -> bool:
        encoded_key, now_ts, window, limit, member = self._lua_args(key, policy)
        result = await self.async_client.eval(RATE_LIMITER_LUA, 1, encoded_key, now_ts, window, limit, member)
        return bool(result)

    def _log_retry(self, key: str, exc: BaseException, attempt: int) -> float:
        """Log a retryable Redis failure and return the delay to wait."""
        delay = _backoff_delay(attempt)
        logger.warning(
            "Redis rate limiter check failed, retrying with backoff",
            extra={
                "rate_limiter_key": key,
                "backend": "redis",
                "error_type": type(exc).__name__,
                "attempt": attempt,
                "max_attempts": REDIS_MAX_ATTEMPTS,
                "retry_delay_seconds": delay,
            },
        )
        return delay

    def _log_unavailable(self, key: str, exc: BaseException, attempt: int, backend: str) -> None:
        """Log that Redis stayed unavailable and the circuit is tripping."""
        logger.warning(
            "Redis rate limiter unavailable after retries, falling back to %s backend",
            backend,
            extra={
                "rate_limiter_key": key,
                "backend": backend,
                "error_type": type(exc).__name__,
                "attempt": attempt,
                "max_attempts": REDIS_MAX_ATTEMPTS,
            },
        )

    def _eval_with_retry(self, key: str, policy: RateLimitPolicy) -> bool:
        """Run :meth:`_eval`, retrying transient failures with backoff."""
        for attempt in range(1, REDIS_MAX_ATTEMPTS + 1):
            try:
                return self._eval(key, policy)
            except RETRYABLE_REDIS_ERRORS as exc:
                if attempt >= REDIS_MAX_ATTEMPTS:
                    raise
                self._sleep(self._log_retry(key, exc, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _eval_async_with_retry(self, key: str, policy: RateLimitPolicy) -> bool:
        """Run :meth:`_eval_async`, retrying transient failures with backoff."""
        for attempt in range(1, REDIS_MAX_ATTEMPTS + 1):
            try:
                return await self._eval_async(key, policy)
            except RETRYABLE_REDIS_ERRORS as exc:
                if attempt >= REDIS_MAX_ATTEMPTS:
                    raise
                delay = self._log_retry(key, exc, attempt)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _run_coroutine(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("async event loop running; use is_allowed_async")

    def is_allowed(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> bool:
        self._reset_circuit()
        safe_key = sanitize_rate_limit_key(key)
        resolved = resolve_policy(safe_key, policy)
        scope = rate_limit_scope(safe_key)

        if db is not None:
            ledger_allowed = record_rate_limit_attempt(db, safe_key, resolved.limit, resolved.window_seconds)
            if not ledger_allowed:
                metrics.increment_counter(
                    "rate_limiter_requests_total",
                    tags={"backend": "ledger", "scope": scope, "outcome": "rejected"},
                )
                return False
            if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
                metrics.increment_counter(
                    "rate_limiter_requests_total",
                    tags={"backend": "ledger", "scope": scope, "outcome": "allowed"},
                )
                return True
            if self._is_circuit_open():
                metrics.increment_counter(
                    "rate_limiter_requests_total",
                    tags={"backend": "ledger", "scope": scope, "outcome": "allowed"},
                )
                return True
            try:
                redis_allowed = self._eval_with_retry(safe_key, resolved)
            except RETRYABLE_REDIS_ERRORS as exc:
                self._log_unavailable(safe_key, exc, REDIS_MAX_ATTEMPTS, "persistent attempt ledger")
                self._trip_circuit()
                metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "redis_error"})
                metrics.increment_counter(
                    "rate_limiter_requests_total",
                    tags={"backend": "ledger", "scope": scope, "outcome": "allowed"},
                )
                return True
            except Exception as exc:
                logger.warning(
                    "Unexpected rate limiter error; using persistent attempt ledger",
                    extra={
                        "rate_limiter_key": safe_key,
                        "backend": "ledger",
                        "error_type": type(exc).__name__,
                    },
                )
                self._trip_circuit()
                metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "unexpected_error"})
                metrics.increment_counter(
                    "rate_limiter_requests_total",
                    tags={"backend": "ledger", "scope": scope, "outcome": "allowed"},
                )
                return True
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "redis", "scope": scope, "outcome": "allowed" if redis_allowed else "rejected"},
            )
            if not redis_allowed:
                self._cache_denial(safe_key)
            return redis_allowed

        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return self.fallback.is_allowed(safe_key, policy=resolved)
        if self._is_circuit_open():
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "circuit_open"})
            return self.fallback.is_allowed(safe_key, policy=resolved)
        if self._cached_denied(safe_key):
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "redis", "scope": scope, "outcome": "rejected"},
            )
            return False

        try:
            allowed = self._eval_with_retry(safe_key, resolved)
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "redis", "scope": scope, "outcome": "allowed" if allowed else "rejected"},
            )
            if not allowed:
                self._cache_denial(safe_key)
            else:
                self.deny_cache.pop(safe_key, None)
            return allowed
        except RETRYABLE_REDIS_ERRORS as exc:
            self._log_unavailable(safe_key, exc, REDIS_MAX_ATTEMPTS, "in-memory limiter")
            self._trip_circuit()
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "redis_error"})
            return self.fallback.is_allowed(safe_key, policy=resolved)
        except Exception as exc:
            logger.warning(
                "Unexpected rate limiter error, falling back to in-memory limiter",
                extra={
                    "rate_limiter_key": safe_key,
                    "backend": "memory",
                    "error_type": type(exc).__name__,
                },
            )
            self._trip_circuit()
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "unexpected_error"})
            return self.fallback.is_allowed(safe_key, policy=resolved)

    async def is_allowed_async(self, key: str, policy: RateLimitPolicy | None = None) -> bool:
        self._reset_circuit()
        safe_key = sanitize_rate_limit_key(key)
        resolved = resolve_policy(safe_key, policy)
        scope = rate_limit_scope(safe_key)

        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return self.fallback.is_allowed(safe_key, policy=resolved)
        if self._is_circuit_open():
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "circuit_open"})
            return self.fallback.is_allowed(safe_key, policy=resolved)
        if self._cached_denied(safe_key):
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "redis", "scope": scope, "outcome": "rejected"},
            )
            return False

        try:
            allowed = await self._eval_async_with_retry(safe_key, resolved)
            metrics.increment_counter(
                "rate_limiter_requests_total",
                tags={"backend": "redis", "scope": scope, "outcome": "allowed" if allowed else "rejected"},
            )
            if not allowed:
                self._cache_denial(safe_key)
            else:
                self.deny_cache.pop(safe_key, None)
            return allowed
        except RETRYABLE_REDIS_ERRORS as exc:
            self._log_unavailable(safe_key, exc, REDIS_MAX_ATTEMPTS, "in-memory limiter")
            self._trip_circuit()
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "redis_error"})
            return self.fallback.is_allowed(safe_key, policy=resolved)
        except Exception as exc:
            logger.warning(
                "Unexpected rate limiter error, falling back to in-memory limiter",
                extra={
                    "rate_limiter_key": safe_key,
                    "backend": "memory",
                    "error_type": type(exc).__name__,
                },
            )
            self._trip_circuit()
            metrics.increment_counter("rate_limiter_fallback_total", tags={"reason": "unexpected_error"})
            return self.fallback.is_allowed(safe_key, policy=resolved)

    def check_or_raise(self, key: str, db: Session | None = None, policy: RateLimitPolicy | None = None) -> None:
        """Check the rate limit and raise :class:`RateLimitError` if exceeded."""
        if self.is_allowed(key, db=db, policy=policy):
            return

        circuit_open = self._is_circuit_open()
        logger.warning(
            "Rate limit exceeded",
            extra={
                "rate_limiter_key": sanitize_rate_limit_key(key),
                "backend": "ledger" if db is not None else ("memory" if circuit_open else "redis"),
                "circuit_open": circuit_open,
            },
        )
        raise RateLimitError(sanitize_rate_limit_key(key))


# Shared fallback for RedisRateLimiter instances so that in-memory rate-limiting
# state is consistent across all instances when Redis is unavailable.
_shared_fallback = SimpleRateLimiter()


def create_rate_limiter() -> SimpleRateLimiter | RedisRateLimiter:
    if settings.USE_REDIS_RATE_LIMITER and not settings.CELERY_TASK_ALWAYS_EAGER:
        return RedisRateLimiter()
    return SimpleRateLimiter()


rate_limiter = create_rate_limiter()


def with_rate_limit_responses(
    responses: dict[int | str, dict[str, object]] | None = None,
) -> dict[int | str, dict[str, object]]:
    """Merge endpoint responses with a standard 429 rate-limit response for OpenAPI."""
    merged: dict[int | str, dict[str, object]] = deepcopy(RATE_LIMITED_RESPONSE)
    if responses:
        for code, payload in responses.items():
            if code == 429 and isinstance(payload, dict) and isinstance(merged.get(429), dict):
                existing = merged[429]  # type: ignore[index]
                # Keep custom description but ensure schema/content is documented.
                if "description" in payload:
                    existing["description"] = payload["description"]
                if "content" in payload:
                    existing["content"] = payload["content"]
                else:
                    existing.setdefault("content", RATE_LIMITED_RESPONSE[429]["content"])
            else:
                merged[code] = deepcopy(payload)
    return merged
