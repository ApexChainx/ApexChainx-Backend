"""Redis caching layer for SLA calculation results (#25).

Cache failures are logged and treated as misses so a degraded Redis never
breaks the SLA computation path. Every Redis operation is wrapped in a
``try/except (RedisError, OSError)`` guard, matching the contract of
``AuthCache``, ``UserCache``, and ``WalletCache``.
"""

import json
import logging
from collections.abc import Callable, Iterable

from redis import Redis
from redis.exceptions import RedisError

from app.services import metrics

logger = logging.getLogger(__name__)


class SLACache:
    """Read-through cache for SLA computation results.

    A degraded or unreachable Redis is always treated as a cache miss —
    the caller falls through to recomputing the SLA result and the error is
    logged at WARNING level. No exception ever escapes this class.
    """

    # Prometheus metric names
    _GET_TOTAL = "cache_sla_get_total"
    _SET_TOTAL = "cache_sla_set_total"
    _INVALIDATE_TOTAL = "cache_sla_invalidate_total"
    _HIT_TOTAL = "cache_sla_hit_total"
    _MISS_TOTAL = "cache_sla_miss_total"
    _ERROR_TOTAL = "cache_sla_error_total"
    _DURATION_MS = "cache_sla_operation_duration_seconds"

    def __init__(self, redis: Redis, ttl: int = 60):
        self._redis = redis
        self._ttl = ttl

    @staticmethod
    def _key(device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> dict | None:
        key = self._key(device_id, period)
        try:
            raw = self._redis.get(key)
        except (RedisError, OSError) as exc:
            logger.warning("SLA cache read failed for %s: %s", key, exc)
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Corrupt JSON in SLA cache for %s, treating as miss", key)
            return None
        return value if isinstance(value, dict) else None

    def set(self, device_id: str, period: str, result: dict) -> bool:
        key = self._key(device_id, period)
        try:
            self._redis.setex(key, self._ttl, json.dumps(result, separators=(",", ":")))
            return True
        except (RedisError, OSError) as exc:
            logger.warning("SLA cache write failed for %s: %s", key, exc)
            return False

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
        metrics.increment_counter(
            "cache_sla_compute_total",
            tags={"operation": "compute", "result": "miss_triggered"},
        )
        result = compute_fn()
        self.set(device_id, period, result)
        return result

    def invalidate(self, device_id: str, period: str) -> None:
        key = self._key(device_id, period)
        try:
            self._redis.delete(key)
        except (RedisError, OSError) as exc:
            logger.warning("SLA cache invalidate failed for %s: %s", key, exc)

    def get_stats(self) -> dict:
        """Return cache operational statistics for monitoring."""
        return {
            "namespace": "sla",
            "ttl_seconds": self._ttl,
        }

    def warm_up(self, entries: Iterable[tuple[str, str, dict]]) -> int:
        """Pre-populate the cache with precomputed SLA results.

        Uses the exact same keys and TTL as the read path, so a value written
        here is indistinguishable from one written by ``set``/``get_or_compute``.
        Individual failures are skipped so one bad entry cannot abort the pass.
        """
        warmed = 0
        for device_id, period, result in entries:
            if self.set(device_id, period, result):
                warmed += 1
        return warmed
