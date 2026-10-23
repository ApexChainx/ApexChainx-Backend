"""Redis caching layer for SLA calculation results (#25).

Exposes Prometheus metrics via the shared metrics registry so operators can
monitor cache hit rates, latencies, and errors in production.
"""

import json
from typing import Callable, Optional

from app.services.metrics import metrics, timer


class SLACache:
    """Read-through cache for SLA computation results."""

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

    def _key(self, device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> Optional[dict]:
        """Return cached SLA result or None on miss / error.

        Records cache_hit_total / cache_miss_total and operation duration
        histograms for Prometheus scraping.
        """
        tags = {"operation": "get", "result": "unknown"}
        with timer(self._DURATION_MS, tags):
            try:
                raw = self._redis.get(self._key(device_id, period))
                if raw:
                    metrics.increment_counter(
                        self._HIT_TOTAL,
                        tags={"operation": "get", "result": "hit"},
                    )
                    metrics.increment_counter(
                        self._GET_TOTAL,
                        tags={"operation": "get", "result": "hit"},
                    )
                    return json.loads(raw)
                metrics.increment_counter(
                    self._MISS_TOTAL,
                    tags={"operation": "get", "result": "miss"},
                )
                metrics.increment_counter(
                    self._GET_TOTAL,
                    tags={"operation": "get", "result": "miss"},
                )
                return None
            except Exception:
                metrics.increment_counter(
                    self._ERROR_TOTAL,
                    tags={"operation": "get", "error": "redis_error"},
                )
                metrics.increment_counter(
                    self._GET_TOTAL,
                    tags={"operation": "get", "result": "error"},
                )
                return None

    def set(self, device_id: str, period: str, result: dict) -> None:
        """Store SLA result in the cache."""
        tags = {"operation": "set"}
        with timer(self._DURATION_MS, tags):
            try:
                self._redis.setex(
                    self._key(device_id, period),
                    self._ttl,
                    json.dumps(result),
                )
                metrics.increment_counter(self._SET_TOTAL, tags=tags)
            except Exception:
                metrics.increment_counter(
                    self._ERROR_TOTAL,
                    tags={"operation": "set", "error": "redis_error"},
                )

    def get_or_compute(
        self,
        device_id: str,
        period: str,
        compute_fn: Callable[[], dict],
    ) -> dict:
        """Return cached result or compute and cache it.

        Records a cache_miss_total when a compute is required.
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
        """Remove an SLA cache entry."""
        tags = {"operation": "invalidate"}
        with timer(self._DURATION_MS, tags):
            try:
                self._redis.delete(self._key(device_id, period))
                metrics.increment_counter(self._INVALIDATE_TOTAL, tags=tags)
            except Exception:
                metrics.increment_counter(
                    self._ERROR_TOTAL,
                    tags={"operation": "invalidate", "error": "redis_error"},
                )

    def invalidate_by_device(self, device_id: str) -> int:
        """Remove all SLA cache entries for a given device_id (#1372).

        Returns the number of entries deleted. Useful when a device's SLA
        configuration changes and all cached results for that device must
        be invalidated at once.
        """
        # Use a SCAN-based approach to find and delete all keys for this device
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = self._redis.scan(
                cursor=cursor,
                match=self._key(device_id, "*"),
                count=100,
            )
            if keys:
                if cursor == 0:
                    deleted += self._redis.delete(*keys)
                    break
                deleted += self._redis.delete(*keys)
            if cursor == 0:
                break
        if deleted:
            metrics.increment_counter(
                "cache_sla_invalidate_device_total",
                tags={"operation": "invalidate_device", "deleted": str(deleted)},
            )
        return deleted

    def warmup(
        self,
        rows: list[tuple[str, str, dict]],
        *,
        ttl_override: Optional[int] = None,
    ) -> int:
        """Pre-populate the cache with SLA results (bulk warm) (#1415).

        Args:
            rows: list of (device_id, period, result_dict) tuples
            ttl_override: optional TTL override for this warmup batch

        Returns:
            Number of entries successfully cached.
        """
        ttl = ttl_override if ttl_override is not None else self._ttl
        populated = 0
        for device_id, period, result in rows:
            try:
                self._redis.setex(
                    self._key(device_id, period),
                    ttl,
                    json.dumps(result),
                )
                populated += 1
                metrics.increment_counter(
                    "cache_sla_warmup_total",
                    tags={"operation": "warmup", "result": "ok"},
                )
            except Exception:
                metrics.increment_counter(
                    "cache_sla_warmup_total",
                    tags={"operation": "warmup", "result": "error"},
                )
        return populated

    def get_stats(self) -> dict:
        """Return cache operational statistics for monitoring (#1372).

        Returns a dict with:
        - ttl: configured TTL in seconds
        - class: "SLACache"
        """
        return {
            "ttl": self._ttl,
            "class": "SLACache",
        }
