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

import asyncio
import hashlib
import logging
import random
from collections import defaultdict
from collections.abc import Callable
from time import sleep as _time_sleep
from time import time
from typing import ClassVar

import redis
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.auth_attempt_ledger import record_rate_limit_attempt

logger = logging.getLogger(__name__)

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

    def is_allowed(self, key: str, db: Session | None = None) -> bool:
        """Check the persistent ledger when an authentication session is available."""
        if db is not None:
            return record_rate_limit_attempt(
                db,
                key,
                settings.AUTH_RATE_LIMIT_REQUESTS,
                settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
            )

        now = time()
        window_start = now - settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
        self.requests[key] = [t for t in self.requests[key] if t > window_start]
        if len(self.requests) > SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD:
            self.cull_expired(now=now)
        if len(self.requests[key]) >= settings.AUTH_RATE_LIMIT_REQUESTS:
            return False

        self.requests[key].append(now)
        return True

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
        window_start = current - settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
        expired = [key for key, hits in self.requests.items() if not hits or hits[-1] <= window_start]
        for key in expired:
            del self.requests[key]
        return len(expired)

    def check_or_raise(self, key: str, db: Session | None = None) -> None:
        """Check the rate limit and raise :class:`RateLimitError` if exceeded.

        Args:
            key: The identifier to rate-limit (for example an IP address).
            db: Optional database session; when supplied the persistent attempt
                ledger is consulted instead of the in-memory window.

        Raises:
            RateLimitError: When the key is over its configured limit.
        """
        if self.is_allowed(key, db=db):
            return

        logger.warning(
            "Rate limit exceeded",
            extra={
                "rate_limiter_key": key,
                "backend": "ledger" if db is not None else "memory",
                "circuit_open": False,
            },
        )
        raise RateLimitError(key)


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

    def _key_namespace(self, key: str) -> str:
        bounded_key = key if len(key) <= 128 else hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"auth_rate_limiter:{bounded_key}"

    def _is_circuit_open(self) -> bool:
        return self.disabled_until is not None and time() < self.disabled_until

    def _trip_circuit(self) -> None:
        self.disabled_until = time() + CIRCUIT_RESET_SECONDS

    def _lua_args(self, key: str) -> tuple[str, int, int, int, str]:
        if not settings.CELERY_BROKER_URL.strip():
            raise RedisError("CELERY_BROKER_URL is empty")
        encoded_key = self._key_namespace(key)
        now_ts = int(time())
        member = f"{now_ts}-{random.random()}"  # nosec B311 - unique sorted-set member, not security
        return (
            encoded_key,
            now_ts,
            settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
            settings.AUTH_RATE_LIMIT_REQUESTS,
            member,
        )

    def _eval(self, key: str) -> bool:
        encoded_key, now_ts, window, limit, member = self._lua_args(key)
        result = self.client.eval(RATE_LIMITER_LUA, 1, encoded_key, now_ts, window, limit, member)
        return bool(result)

    async def _eval_async(self, key: str) -> bool:
        encoded_key, now_ts, window, limit, member = self._lua_args(key)
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

    def _eval_with_retry(self, key: str) -> bool:
        """Run :meth:`_eval`, retrying transient failures with backoff.

        Raises:
            Exception: The last error once every attempt has failed.
        """
        for attempt in range(1, REDIS_MAX_ATTEMPTS + 1):
            try:
                return self._eval(key)
            except RETRYABLE_REDIS_ERRORS as exc:
                if attempt >= REDIS_MAX_ATTEMPTS:
                    raise
                self._sleep(self._log_retry(key, exc, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _eval_async_with_retry(self, key: str) -> bool:
        """Run :meth:`_eval_async`, retrying transient failures with backoff.

        Raises:
            Exception: The last error once every attempt has failed.
        """
        for attempt in range(1, REDIS_MAX_ATTEMPTS + 1):
            try:
                return await self._eval_async(key)
            except RETRYABLE_REDIS_ERRORS as exc:
                if attempt >= REDIS_MAX_ATTEMPTS:
                    raise
                delay = self._log_retry(key, exc, attempt)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def is_allowed(self, key: str, db: Session | None = None) -> bool:
        if db is not None:
            if not record_rate_limit_attempt(
                db,
                key,
                settings.AUTH_RATE_LIMIT_REQUESTS,
                settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
            ):
                return False
            if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
                return True
            if self._is_circuit_open():
                return True

            try:
                return self._run_coroutine(self._is_allowed_async(key))
            except RETRYABLE_REDIS_ERRORS as exc:
                self._log_unavailable(key, exc, REDIS_MAX_ATTEMPTS, "persistent attempt ledger")
                self._trip_circuit()
                return True
            except Exception as exc:  # noqa: BLE001 - never let the limiter break auth
                logger.warning(
                    "Unexpected rate limiter error; using persistent attempt ledger",
                    extra={
                        "rate_limiter_key": key,
                        "backend": "ledger",
                        "error_type": type(exc).__name__,
                    },
                )
                self._trip_circuit()
                return True

        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return self.fallback.is_allowed(key)
        if self._is_circuit_open():
            return self.fallback.is_allowed(key)

        try:
            return self._eval_with_retry(key)
        except RETRYABLE_REDIS_ERRORS as exc:
            self._log_unavailable(key, exc, REDIS_MAX_ATTEMPTS, "in-memory limiter")
            self._trip_circuit()
            return self.fallback.is_allowed(key)
        except Exception as exc:  # noqa: BLE001 - never let the limiter break auth
            logger.warning(
                "Unexpected rate limiter error, falling back to in-memory limiter",
                extra={
                    "rate_limiter_key": key,
                    "backend": "memory",
                    "error_type": type(exc).__name__,
                },
            )
            self._trip_circuit()
            return self.fallback.is_allowed(key)

    async def is_allowed_async(self, key: str) -> bool:
        if not settings.USE_REDIS_RATE_LIMITER or settings.CELERY_TASK_ALWAYS_EAGER:
            return self.fallback.is_allowed(key)

        if self._is_circuit_open():
            return self.fallback.is_allowed(key)

        try:
            return await self._eval_async_with_retry(key)
        except RETRYABLE_REDIS_ERRORS as exc:
            self._log_unavailable(key, exc, REDIS_MAX_ATTEMPTS, "in-memory limiter")
            self._trip_circuit()
            return self.fallback.is_allowed(key)
        except Exception as exc:  # noqa: BLE001 - never let the limiter break auth
            logger.warning(
                "Unexpected rate limiter error, falling back to in-memory limiter",
                extra={
                    "rate_limiter_key": key,
                    "backend": "memory",
                    "error_type": type(exc).__name__,
                },
            )
            self._trip_circuit()
            return self.fallback.is_allowed(key)

    def check_or_raise(self, key: str, db: Session | None = None) -> None:
        """Check the rate limit and raise :class:`RateLimitError` if exceeded.

        Args:
            key: The identifier to rate-limit (for example an IP address).
            db: Optional database session forwarded to :meth:`is_allowed`.

        Raises:
            RateLimitError: When the key is over its configured limit, whether
                the decision came from Redis, the ledger, or the in-memory
                fallback.
        """
        if self.is_allowed(key, db=db):
            return

        circuit_open = self._is_circuit_open()
        logger.warning(
            "Rate limit exceeded",
            extra={
                "rate_limiter_key": key,
                "backend": "ledger" if db is not None else ("memory" if circuit_open else "redis"),
                "circuit_open": circuit_open,
            },
        )
        raise RateLimitError(key)


# Shared fallback for RedisRateLimiter instances so that in-memory rate-limiting
# state is consistent across all instances when Redis is unavailable.
_shared_fallback = SimpleRateLimiter()

rate_limiter = (
    RedisRateLimiter()
    if settings.USE_REDIS_RATE_LIMITER and not settings.CELERY_TASK_ALWAYS_EAGER
    else SimpleRateLimiter()
)
