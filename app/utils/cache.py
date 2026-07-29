"""Lightweight in-process TTL cache for dashboard aggregations.

Usage:
    cache = TTLCache(ttl_seconds=30)
    result = cache.get("key")
    cache.set("key", value)
    cache.invalidate("key")   # call after writes that affect cached data
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any


class TTLCache:
    """Thread-safe in-memory cache with per-entry TTL.

    Staleness behaviour: entries expire after `ttl_seconds`. Callers that
    need fresh data after a write should call `invalidate` explicitly.
    An empty or recently-changed dataset is handled correctly because the
    cache is bypassed on a miss and always stores the latest computed value.
    """

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
