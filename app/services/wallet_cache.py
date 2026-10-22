"""Redis-backed read-through cache for wallet reads (#29).

Cache failures are logged and treated as misses so a degraded Redis never
breaks the wallet lookup path.  This mirrors the error-handling contract of
``AuthCache`` and ``UserCache``: every Redis operation is wrapped in a
``try/except (RedisError, OSError)`` guard and logged at WARNING level.
"""

import json
import logging

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class WalletCache:
    """Read-through cache wrapping Redis for wallet lookups.

    A degraded or unreachable Redis is always treated as a cache miss —
    the caller falls through to the database and the error is logged.
    No exception ever escapes this class.
    """

    def __init__(self, redis_client, ttl: int = 60):
        self._redis = redis_client
        self._ttl = ttl

    def _key(self, address: str) -> str:
        return f"wallet:{address}"

    def get(self, address: str) -> dict | None:
        """Return the cached wallet dict for *address*, or ``None`` on a miss.

        Returns ``None`` on Redis errors or corrupt JSON — both are logged.
        """
        try:
            raw = self._redis.get(self._key(address))
        except (RedisError, OSError) as exc:
            logger.warning("Wallet cache read failed for %s: %s", address, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(
                "Corrupt JSON in wallet cache for key %s, treating as miss", address
            )
            return None

    def set(self, address: str, data: dict) -> None:
        """Write *data* into the cache under *address*.

        Logs and swallows Redis errors so a write failure never breaks the
        request path.
        """
        try:
            self._redis.setex(self._key(address), self._ttl, json.dumps(data))
        except (RedisError, OSError) as exc:
            logger.warning("Wallet cache write failed for %s: %s", address, exc)

    def invalidate(self, address: str) -> None:
        """Remove the cached entry for *address*.

        Logs and swallows Redis errors.
        """
        try:
            self._redis.delete(self._key(address))
        except (RedisError, OSError) as exc:
            logger.warning("Wallet cache invalidate failed for %s: %s", address, exc)
