"""Redis caching layer for SLA calculation results (#25)."""

import hashlib
import json
import time
from typing import Callable, Optional

from redis import Redis


class SLACache:
    """Read-through cache for SLA computation results."""

    def __init__(self, redis: Redis, ttl: int = 60):
        self._redis = redis
        self._ttl = ttl

    def _key(self, device_id: str, period: str) -> str:
        return f"sla:{device_id}:{period}"

    def get(self, device_id: str, period: str) -> Optional[dict]:
        raw = self._redis.get(self._key(device_id, period))
        if raw:
            return json.loads(raw)
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
