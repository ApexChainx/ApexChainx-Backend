"""Lightweight in-process TTL cache for dashboard aggregations.

Usage:
    cache = TTLCache(ttl_seconds=30)
    result = cache.get("key")
    cache.set("key", value)
    cache.invalidate("key")   # call after writes that affect cached data

Exposes Prometheus metrics via the shared metrics registry so operators can
monitor in-process cache hit rates and operation counts.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Optional

from app.services.metrics import metrics


class TTLCache:
    """Thread-safe in-process cache with per-entry TTL.

    Staleness behaviour: entries expire after `ttl_seconds`. Callers that
    need fresh data after a write should call `invalidate` explicitly.
    An empty or recently-changed dataset is handled correctly because the
    cache is bypassed on a miss and always stores the latest computed value.
    """

    # Prometheus metric names
    _GET_TOTAL = "cache_inprocess_get_total"
    _SET_TOTAL = "cache_inprocess_set_total"
    _INVALIDATE_TOTAL = "cache_inprocess_invalidate_total"
    _INVALIDATE_PREFIX_TOTAL = "cache_inprocess_invalidate_prefix_total"
    _HIT_TOTAL = "cache_inprocess_hit_total"
    _MISS_TOTAL = "cache_inprocess_miss_total"
    _EXPIRED_TOTAL = "cache_inprocess_expired_total"
    _SIZE_GAUGE = "cache_inprocess_entries"

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None on miss / expiry.

        Records cache hit / miss / expired counters for Prometheus.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                metrics.increment_counter(self._MISS_TOTAL, tags={"operation": "get", "result": "miss"})
                metrics.increment_counter(self._GET_TOTAL, tags={"operation": "get", "result": "miss"})
                self._update_size_gauge()
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                metrics.increment_counter(
                    self._EXPIRED_TOTAL,
                    tags={"operation": "get", "result": "expired"},
                )
                metrics.increment_counter(
                    self._MISS_TOTAL,
                    tags={"operation": "get", "result": "expired"},
                )
                metrics.increment_counter(
                    self._GET_TOTAL,
                    tags={"operation": "get", "result": "expired"},
                )
                self._update_size_gauge()
                return None
            metrics.increment_counter(
                self._HIT_TOTAL,
                tags={"operation": "get", "result": "hit"},
            )
            metrics.increment_counter(
                self._GET_TOTAL,
                tags={"operation": "get", "result": "hit"},
            )
            self._update_size_gauge()
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value in the cache."""
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)
            metrics.increment_counter(self._SET_TOTAL, tags={"operation": "set"})
            self._update_size_gauge()

    def invalidate(self, key: str) -> None:
        """Remove a single cache entry."""
        with self._lock:
            self._store.pop(key, None)
            metrics.increment_counter(
                self._INVALIDATE_TOTAL,
                tags={"operation": "invalidate"},
            )
            self._update_size_gauge()

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all cache entries whose key starts with `prefix`."""
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            metrics.increment_counter(
                self._INVALIDATE_PREFIX_TOTAL,
                tags={"operation": "invalidate_prefix", "removed": str(len(keys))},
            )
            self._update_size_gauge()

    def _update_size_gauge(self) -> None:
        """Update the in-process cache entry count gauge."""
        metrics.set_gauge(self._SIZE_GAUGE, len(self._store))

    def get_stats(self) -> dict:
        """Return cache operational statistics for monitoring (#1372).

        Returns a dict with:
        - ttl: configured TTL in seconds
        - entries: current number of entries in the cache
        - class: "TTLCache"
        """
        with self._lock:
            return {
                "ttl": self._ttl,
                "entries": len(self._store),
                "class": "TTLCache",
            }

    def warmup(self, items: dict[str, Any]) -> int:
        """Pre-populate the cache with multiple entries (bulk warm) (#1415).

        Args:
            items: dict mapping cache keys to values

        Returns:
            Number of entries successfully cached.
        """
        with self._lock:
            populated = 0
            for key, value in items.items():
                self._store[key] = (value, time.monotonic() + self._ttl)
                populated += 1
            metrics.increment_counter(
                "cache_inprocess_warmup_total",
                tags={"operation": "warmup", "result": "ok", "count": str(populated)},
            )
            self._update_size_gauge()
            return populated
