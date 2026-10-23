"""Redis caching layer for SLA calculation results (#25)."""

from collections.abc import Callable, Iterable

from redis import Redis

from app.services.cache_layer import CacheLayer

class SLACache:
    """Read-through cache for SLA computation results."""

    def __init__(self, redis: Redis, ttl: int = 60):
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
        """Return cached result or compute and cache it."""
        cached = self.get(device_id, period)
        if cached is not None:
            return cached
        result = compute_fn()
        self.set(device_id, period, result)
        return result

    def invalidate(self, device_id: str, period: str) -> None:
        self._cache.delete(f"{device_id}:{period}")

    def warm_up(self, entries: Iterable[tuple[str, str, dict]]) -> int:
        """Pre-populate the cache with precomputed results (#566).

        Uses the exact same keys and TTL as the read path, so a value written
        here is indistinguishable from one written by ``set``/``get_or_compute``
        during a request. Returns the number of entries written.
        """
        warmed = 0
        for device_id, period, result in entries:
            self.set(device_id, period, result)
            warmed += 1
        return warmed
