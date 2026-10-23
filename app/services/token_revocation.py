"""Token revocation service backed by Redis.

Stores revoked token hashes in Redis with TTL matching the token's remaining
lifetime.  Redis failures are logged and treated as non-fatal:

* ``revoke`` logs a warning and returns without raising — the token remains
  technically valid until its natural expiry, but the revocation attempt is
  recorded in the log for audit purposes.
* ``is_revoked`` logs a warning and returns ``False`` (not revoked) — failing
  open keeps the authentication path available during a Redis outage.  The
  trade-off is documented here so reviewers can make an informed decision.

Both behaviours mirror the "Redis failure = cache miss" contract used by
``AuthCache``, ``UserCache``, ``WalletCache``, and ``SLACache``.
"""

import logging

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

_revocation_redis: Redis | None = None


def _get_redis() -> Redis:
    global _revocation_redis
    if _revocation_redis is None:
        _revocation_redis = Redis.from_url(settings.CELERY_BROKER_URL)
    return _revocation_redis


def revoke(token_hash: str, ttl_seconds: int) -> None:
    """Store a token hash in the revocation list with the given TTL.

    Logs and swallows Redis errors so a write failure never crashes the
    logout / token-rotation path.  The revocation attempt is still visible
    in the structured warning log.
    """
    key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{token_hash}"
    try:
        _get_redis().setex(key, ttl_seconds, "1")
    except (RedisError, OSError) as exc:
        logger.warning(
            "Token revocation write failed for hash %s: %s — token may not be revoked",
            token_hash,
            exc,
        )


def is_revoked(token_hash: str) -> bool:
    """Check if a token hash has been revoked.

    Returns ``False`` on Redis errors (fail-open) so that a Redis outage does
    not lock every authenticated user out of the API.  The failure is logged
    so it is visible in alerting.
    """
    key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{token_hash}"
    try:
        return _get_redis().exists(key) > 0
    except (RedisError, OSError) as exc:
        logger.warning(
            "Token revocation read failed for hash %s: %s — assuming not revoked",
            token_hash,
            exc,
        )
        return False
