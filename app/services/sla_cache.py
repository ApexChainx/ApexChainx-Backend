"""Redis caching layer for SLA calculation results (#25).

Cache failures are logged and treated as misses so a degraded Redis never
breaks the SLA computation path.  Every Redis operation is wrapped in a
``try/except (RedisError, OSError)`` guard, matching the contract of
``AuthCache``, ``UserCache``, and ``WalletCache``.
"""

from collections.abc import Callable, Iterable

from redis import Redis
from redis.exceptions import RedisError

from app.services.cache_layer import CacheLayer

class SLACache:
    """Read-through cache for SLA computation results.

    A degraded or unreachable Redis is always treated as a cache miss —
    the caller falls through to recomputing the SLA result and the error is
    logged at WARNING level.  No exception ever escapes this class.
    """

    # Prometheus metric names
    _GET_TOTAL = "cache_sla_get_total"
    _SET_TOTAL = "cache_sla_set_total"
    _INVALIDATE_TOTAL = "cache_sla_invalidate_total"
    _HIT_TOTAL = "cache_sla_hit_total"
    _MISS_TOTAL = "cache_sla_miss_total"
    _ERROR_TOTAL = "cache_sla_error_total"
    _DURATION_MS = "cache_sla_operation_duration_seconds"

    def __init__(self, redis, ttl: int = 60):
        self._redis = redis
        self._ttl = ttl
        self._cache = CacheLayer(redis, namespace="sla", ttl_seconds=ttl)

    def _key(self, device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> dict | None:
        value = self._cache.get(f"{device_id}:{period}")
        return value if isinstance(value, dict) else None

    def set(self, device_id: str, period: str, result: dict) -> None:
        self._cache.set(f"{device_id}:{period}", result)

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
        self._cache.delete(f"{device_id}:{period}")

    def get_stats(self) -> dict:
        """Return cache operational statistics for monitoring (#1372).

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
