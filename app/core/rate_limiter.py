"""Auth rate limiting with Redis and persistent database enforcement."""

import hashlib
import logging
import random
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from time import time
from typing import ClassVar

import redis
import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.auth_attempt_ledger import record_rate_limit_attempt

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

# Keys are built from client-controlled input (IPs from forwarded headers, user
# ids). Anything outside this set is replaced so a key cannot smuggle control
# characters into logs or alias another namespace in Redis.
RATE_LIMIT_KEY_MAX_LENGTH = 128
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.:@\-]")
EMPTY_RATE_LIMIT_KEY = "anonymous"

# How long a Redis "deny" verdict is cached in-process. While cached, repeat
# requests for a blocked key are rejected without a Redis round trip; the TTL is
# short (and never longer than the window) so a key is unblocked promptly.
REDIS_DENY_CACHE_SECONDS = 5
REDIS_DENY_CACHE_MAX_KEYS = 10_000

REDIS_CIRCUIT_BREAKER_SECONDS = 30


def sanitize_rate_limit_key(key: object) -> str:
    """Normalise a rate-limit key into a bounded, printable, namespace-safe string.

    Unicode is NFKC-normalised, surrounding whitespace is stripped and any
    character outside ``[A-Za-z0-9_.:@-]`` becomes ``_``. Over-long keys are
    replaced by their SHA-256 digest, and empty keys collapse to a single
    ``anonymous`` bucket rather than raising.
    """
    text = key if isinstance(key, str) else ("" if key is None else str(key))
    text = unicodedata.normalize("NFKC", text).strip()
    text = _UNSAFE_KEY_CHARS.sub("_", text)
    if not text:
        return EMPTY_RATE_LIMIT_KEY
    if len(text) > RATE_LIMIT_KEY_MAX_LENGTH:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text


def _redis_enabled() -> bool:
    return settings.USE_REDIS_RATE_LIMITER and not settings.CELERY_TASK_ALWAYS_EAGER


def _record_ledger_attempt(db: Session, key: str) -> bool:
    return record_rate_limit_attempt(
        db,
        key,
        settings.AUTH_RATE_LIMIT_REQUESTS,
        settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    )


class SimpleRateLimiter:
    _shared: ClassVar[dict[str, list[float]]] = defaultdict(list)

    def __init__(self) -> None:
        self.requests = SimpleRateLimiter._shared

    def is_allowed(self, key: str, db: Session | None = None) -> bool:
        """Check the persistent ledger when an authentication session is available."""
        key = sanitize_rate_limit_key(key)
        if db is not None:
            return _record_ledger_attempt(db, key)

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


class RedisRateLimiter:
    def __init__(self) -> None:
        self.fallback = _shared_fallback
        self.disabled_until: float | None = None
        # Short-lived cache of keys Redis has rejected: key -> expiry timestamp.
        self.deny_cache: dict[str, float] = {}
        # Shared, lazily-connected clients (sync for sync callers, async for
        # async callers). No event loop is created per request.
        self.client = redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        self.async_client = redis_async.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)

    def _key_namespace(self, key: str) -> str:
        return f"auth_rate_limiter:{sanitize_rate_limit_key(key)}"

    # Circuit breaker ------------------------------------------------------

    def _is_circuit_open(self) -> bool:
        return self.disabled_until is not None and time() < self.disabled_until

    def _trip_circuit(self) -> None:
        self.disabled_until = time() + REDIS_CIRCUIT_BREAKER_SECONDS

    def _redis_usable(self) -> bool:
        return _redis_enabled() and not self._is_circuit_open()

    # Deny-verdict cache ---------------------------------------------------

    def _deny_cache_ttl(self) -> float:
        return min(REDIS_DENY_CACHE_SECONDS, settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)

    def _is_cached_deny(self, key: str) -> bool:
        expires_at = self.deny_cache.get(key)
        if expires_at is None:
            return False
        if time() < expires_at:
            return True
        self.deny_cache.pop(key, None)
        return False

    def _cache_verdict(self, key: str, allowed: bool) -> bool:
        if allowed:
            self.deny_cache.pop(key, None)
            return True
        now = time()
        if len(self.deny_cache) >= REDIS_DENY_CACHE_MAX_KEYS:
            self.deny_cache = {k: exp for k, exp in self.deny_cache.items() if exp > now}
        if len(self.deny_cache) < REDIS_DENY_CACHE_MAX_KEYS:
            self.deny_cache[key] = now + self._deny_cache_ttl()
        return False

    def clear_cache(self) -> None:
        self.deny_cache.clear()

    # Redis evaluation -----------------------------------------------------

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

    def _on_redis_failure(self, exc: Exception, on_failure: Callable[[], bool]) -> bool:
        if isinstance(exc, (RedisError, OSError)):
            logger.warning("Redis rate limiter unavailable, using fallback: %s", exc)
        else:
            logger.warning("Unexpected rate limiter error, using fallback: %s", exc)
        self._trip_circuit()
        return on_failure()

    def _check_redis(self, key: str, on_failure: Callable[[], bool]) -> bool:
        if not self._redis_usable():
            return on_failure()
        if self._is_cached_deny(key):
            return False
        try:
            return self._cache_verdict(key, self._eval(key))
        except Exception as exc:  # noqa: BLE001 - any failure degrades to the fallback
            return self._on_redis_failure(exc, on_failure)

    async def _check_redis_async(self, key: str, on_failure: Callable[[], bool]) -> bool:
        if not self._redis_usable():
            return on_failure()
        if self._is_cached_deny(key):
            return False
        try:
            return self._cache_verdict(key, await self._eval_async(key))
        except Exception as exc:  # noqa: BLE001 - any failure degrades to the fallback
            return self._on_redis_failure(exc, on_failure)

    # Public API -----------------------------------------------------------

    def is_allowed(self, key: str, db: Session | None = None) -> bool:
        key = sanitize_rate_limit_key(key)
        if db is not None:
            # The persistent ledger is authoritative; Redis adds a cross-worker
            # check on top and is skipped (allow) when unavailable.
            if not _record_ledger_attempt(db, key):
                return False
            return self._check_redis(key, on_failure=lambda: True)
        return self._check_redis(key, on_failure=lambda: self.fallback.is_allowed(key))

    async def is_allowed_async(self, key: str) -> bool:
        key = sanitize_rate_limit_key(key)
        return await self._check_redis_async(key, on_failure=lambda: self.fallback.is_allowed(key))


# Shared fallback for RedisRateLimiter instances so that in-memory rate-limiting
# state is consistent across all instances when Redis is unavailable.
_shared_fallback = SimpleRateLimiter()

rate_limiter = RedisRateLimiter() if _redis_enabled() else SimpleRateLimiter()
