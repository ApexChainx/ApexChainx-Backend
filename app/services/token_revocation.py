"""Token revocation service backed by Redis.

Stores revoked token hashes in Redis with TTL matching the token's remaining
lifetime.

Redis-outage policy: revocation is not allowed to take authentication down
with it. Every other Redis consumer in this codebase (rate limiter,
idempotency middleware, single-use token store, memo uniqueness check)
degrades to an in-process fallback with a warning and a short circuit
breaker instead of failing the request, and THREAT_MODEL.md documents that
as the house policy. This module now does the same:

* while Redis is unreachable, ``is_revoked`` fails open (treated as not
  revoked) so authenticated traffic keeps flowing;
* ``revoke`` always records the hash in a bounded in-process fallback with
  the same TTL, so ``POST /auth/revoke`` keeps its contract within the
  process even during an outage;
* a 30-second circuit breaker stops the per-request connection churn while
  Redis is down.

The fallback is per-process and bounded: a Redis outage degrades revocation
visibility to this process's own revocations rather than turning every
authenticated request into a 500. A multi-worker deployment should restore
Redis to regain cross-worker revocation.
"""

import logging
import time

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

_revocation_redis: Redis | None = None

# Circuit breaker (same shape as the idempotency middleware's #314 breaker):
# seconds to skip Redis entirely after a failure before retrying.
_CIRCUIT_RESET_SECONDS = 30
_unavailable_until = 0.0

# Bounded in-process fallback: token_hash -> monotonic expiry. Entries are
# dropped on read once expired and the dict is capped so a long outage with
# heavy revocation traffic cannot grow it without bound.
_local_revocations: dict[str, float] = {}
_LOCAL_REVOCATIONS_MAX = 10_000


def _get_redis() -> Redis:
    global _revocation_redis
    if _revocation_redis is None:
        _revocation_redis = Redis.from_url(settings.CELERY_BROKER_URL)
    return _revocation_redis


def _key(token_hash: str) -> str:
    return f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{token_hash}"


def _redis_down(exc: Exception) -> None:
    global _unavailable_until
    _unavailable_until = time.monotonic() + _CIRCUIT_RESET_SECONDS
    logger.warning(
        "Token revocation store unavailable, using in-process fallback for %ds: %s",
        _CIRCUIT_RESET_SECONDS,
        exc,
    )


def _redis_up() -> bool:
    return time.monotonic() >= _unavailable_until


def _remember_locally(token_hash: str, ttl_seconds: int) -> None:
    now = time.monotonic()
    if len(_local_revocations) >= _LOCAL_REVOCATIONS_MAX:
        for key in [k for k, expiry in _local_revocations.items() if expiry <= now]:
            del _local_revocations[key]
        while len(_local_revocations) >= _LOCAL_REVOCATIONS_MAX:
            _local_revocations.pop(next(iter(_local_revocations)))
    _local_revocations[token_hash] = now + ttl_seconds


def revoke(token_hash: str, ttl_seconds: int) -> None:
    """Store a token hash in the revocation list with the given TTL."""
    _remember_locally(token_hash, ttl_seconds)
    if not _redis_up():
        return
    try:
        _get_redis().setex(_key(token_hash), ttl_seconds, "1")
    except (RedisError, OSError, ValueError) as exc:
        _redis_down(exc)


def is_revoked(token_hash: str) -> bool:
    """Check if a token hash has been revoked.

    Fails open when the revocation store is unreachable: an outage must not
    500 every authenticated request, it only degrades cross-process
    revocation visibility (see module docstring).
    """
    expiry = _local_revocations.get(token_hash)
    if expiry is not None:
        if expiry > time.monotonic():
            return True
        _local_revocations.pop(token_hash, None)

    if not _redis_up():
        return False
    try:
        return _get_redis().exists(_key(token_hash)) > 0
    except (RedisError, OSError, ValueError) as exc:
        _redis_down(exc)
        return False
