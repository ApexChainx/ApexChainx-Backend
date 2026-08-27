"""Redis-backed read-through cache for wallet reads (#29)."""

import json
import logging

logger = logging.getLogger(__name__)


class WalletCache:
    """Read-through cache wrapping Redis for wallet lookups."""

    def __init__(self, redis_client, ttl: int = 60):
        self._redis = redis_client
        self._ttl = ttl

    def _key(self, address: str) -> str:
        return f"wallet:{address}"

    def get(self, address: str) -> dict | None:
        raw = self._redis.get(self._key(address))
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Corrupt JSON in wallet cache for key %s, treating as miss", address)
                return None
        return None

    def set(self, address: str, data: dict) -> None:
        self._redis.setex(self._key(address), self._ttl, json.dumps(data))

    def invalidate(self, address: str) -> None:
        self._redis.delete(self._key(address))
