"""Redis caching layer for SLA calculation results (#25).

Cache failures are logged and treated as misses so a degraded Redis never
breaks the SLA computation path.  Every Redis operation is wrapped in a
``try/except (RedisError, OSError)`` guard, matching the contract of
``AuthCache``, ``UserCache``, and ``WalletCache``.
"""

import json
import logging
from collections.abc import Callable, Iterable

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class SLACache:
    """Read-through cache for SLA computation results.

    A degraded or unreachable Redis is always treated as a cache miss —
    the caller falls through to recomputing the SLA result and the error is
    logged at WARNING level.  No exception ever escapes this class.
    """

    def __init__(self, redis: Redis, ttl: int = 60):
        self._redis = redis
        self._ttl = ttl

    def _key(self, device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> dict | None:
        """Return the cached SLA result, or ``None`` on a miss.

        Returns ``None`` on Redis errors or corrupt JSON — both are logged.
        """
        try:
            raw = self._redis.get(self._key(device_id, period))
        except (RedisError, OSError) as exc:
            logger.warning("SLA cache read failed for %s/%s: %s", device_id, period, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(
                "Corrupt JSON in SLA cache for key %s:%s, treating as miss",
                device_id,
                period,
            )
            return None

    def set(self, device_id: str, period: str, result: dict) -> None:
        """Write *result* into the cache for *device_id* / *period*.

        Logs and swallows Redis errors so a write failure never breaks the
        request path.
        """
        try:
            self._redis.setex(
                self._key(device_id, period),
                self._ttl,
                json.dumps(result),
            )
        except (RedisError, OSError) as exc:
            logger.warning("SLA cache write failed for %s/%s: %s", device_id, period, exc)

    def get_or_compute(
        self,
        device_id: str,
        period: str,
        compute_fn: Callable[[], dict],
    ) -> dict:
        """Return the cached result or compute, cache, and return it.

        If the cache write fails the computed value is still returned — the
        next call will recompute rather than serve stale or missing data.
        """
        cached = self.get(device_id, period)
        if cached is not None:
            return cached
        result = compute_fn()
        self.set(device_id, period, result)
        return result

    def invalidate(self, device_id: str, period: str) -> None:
        """Remove the cached entry for *device_id* / *period*.

        Logs and swallows Redis errors.
        """
        try:
            self._redis.delete(self._key(device_id, period))
        except (RedisError, OSError) as exc:
            logger.warning(
                "SLA cache invalidate failed for %s/%s: %s", device_id, period, exc
            )

    def warm_up(self, entries: Iterable[tuple[str, str, dict]]) -> int:
        """Pre-populate the cache with precomputed results (#566).

        Uses the exact same keys and TTL as the read path, so a value written
        here is indistinguishable from one written by ``set``/``get_or_compute``
        during a request.

        Individual write failures are logged and skipped so a single bad entry
        cannot abort the whole warm-up pass.  Returns the number of entries
        successfully written.
        """
        warmed = 0
        for device_id, period, result in entries:
            try:
                self.set(device_id, period, result)
                warmed += 1
            except Exception:  # noqa: BLE001
                logger.warning(
                    "SLA cache warm-up skipped entry %s/%s due to unexpected error",
                    device_id,
                    period,
                )
        return warmed
