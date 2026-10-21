"""Redis caching layer for SLA calculation results (#25)."""

import json
import logging
from collections.abc import Callable, Iterable

from redis import Redis

logger = logging.getLogger(__name__)


class SLACache:
    """Read-through cache for SLA computation results."""

    def __init__(self, redis: Redis, ttl: int = 60):
        self._redis = redis
        self._ttl = ttl

    def _key(self, device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> dict | None:
        raw = self._redis.get(self._key(device_id, period))
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Corrupt JSON in SLA cache for key %s:%s, treating as miss", device_id, period)
                return None
        return None

    def set(self, device_id: str, period: str, result: dict) -> None:
        self._redis.setex(
            self._key(device_id, period),
            self._ttl,
            json.dumps(result),
        )

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
        self._redis.delete(self._key(device_id, period))

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
