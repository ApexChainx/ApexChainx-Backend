"""Redis-backed read-through cache for wallet reads (#29).

Exposes Prometheus metrics via the shared metrics registry so operators can
monitor cache hit rates, latencies, and errors in production.
"""

from typing import Optional

from app.services.metrics import metrics, timer


class WalletCache:
    """Read-through cache wrapping Redis for wallet lookups."""

    # Prometheus metric names (namespaced under cache_ for clarity)
    _GET_TOTAL = "cache_wallet_get_total"
    _SET_TOTAL = "cache_wallet_set_total"
    _INVALIDATE_TOTAL = "cache_wallet_invalidate_total"
    _HIT_TOTAL = "cache_wallet_hit_total"
    _MISS_TOTAL = "cache_wallet_miss_total"
    _ERROR_TOTAL = "cache_wallet_error_total"
    _DURATION_MS = "cache_wallet_operation_duration_seconds"

    def __init__(self, redis_client, ttl: int = 60):
        self._redis = redis_client
        self._ttl = ttl

    def _key(self, address: str) -> str:
        return f"wallet:{address}"

    def get(self, address: str) -> Optional[dict]:
        """Return cached wallet data or None on miss / error.

        Records cache_hit_total / cache_miss_total and cache operation
        duration histograms for Prometheus scraping.
        """
        import json

        tags = {"operation": "get", "result": "unknown"}
        with timer(self._DURATION_MS, tags):
            try:
                raw = self._redis.get(self._key(address))
                if raw:
                    metrics.increment_counter(self._HIT_TOTAL, tags={"operation": "get", "result": "hit"})
                    metrics.increment_counter(self._GET_TOTAL, tags={"operation": "get", "result": "hit"})
                    return json.loads(raw)
                metrics.increment_counter(self._MISS_TOTAL, tags={"operation": "get", "result": "miss"})
                metrics.increment_counter(self._GET_TOTAL, tags={"operation": "get", "result": "miss"})
                return None
            except Exception:
                metrics.increment_counter(self._ERROR_TOTAL, tags={"operation": "get", "error": "redis_error"})
                metrics.increment_counter(self._GET_TOTAL, tags={"operation": "get", "result": "error"})
                return None

    def set(self, address: str, data: dict) -> None:
        """Store wallet data in the cache."""
        import json

        tags = {"operation": "set"}
        with timer(self._DURATION_MS, tags):
            try:
                self._redis.setex(self._key(address), self._ttl, json.dumps(data))
                metrics.increment_counter(self._SET_TOTAL, tags=tags)
            except Exception:
                metrics.increment_counter(
                    self._ERROR_TOTAL,
                    tags={"operation": "set", "error": "redis_error"},
                )

    def invalidate(self, address: str) -> None:
        """Remove a wallet entry from the cache."""
        tags = {"operation": "invalidate"}
        with timer(self._DURATION_MS, tags):
            try:
                self._redis.delete(self._key(address))
                metrics.increment_counter(self._INVALIDATE_TOTAL, tags=tags)
            except Exception:
                metrics.increment_counter(
                    self._ERROR_TOTAL,
                    tags={"operation": "invalidate", "error": "redis_error"},
                )

    def warmup(
        self, rows: list[tuple[str, dict]], *, ttl_override: Optional[int] = None
    ) -> int:
        """Pre-populate the cache with wallet data (bulk warm).

        Returns the number of entries successfully cached.
        Used for cache warming after deployments or cache flushes (#1415).
        """
        import json

        ttl = ttl_override if ttl_override is not None else self._ttl
        populated = 0
        for address, data in rows:
            try:
                self._redis.setex(
                    self._key(address),
                    ttl,
                    json.dumps(data),
                )
                populated += 1
                metrics.increment_counter(
                    "cache_wallet_warmup_total",
                    tags={"operation": "warmup", "result": "ok"},
                )
            except Exception:
                metrics.increment_counter(
                    "cache_wallet_warmup_total",
                    tags={"operation": "warmup", "result": "error"},
                )
        return populated

    def get_stats(self) -> dict:
        """Return cache operational statistics for monitoring (#1372).

        Returns a dict with:
        - ttl: configured TTL in seconds
        - class: "WalletCache"
        """
        return {
            "ttl": self._ttl,
            "class": "WalletCache",
        }
