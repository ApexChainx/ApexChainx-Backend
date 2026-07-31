"""Redis-backed read-through cache for wallet reads (#29)."""


class WalletCache:
    """Read-through cache wrapping Redis for wallet lookups."""

    def __init__(self, redis_client, ttl: int = 60):
        self._redis = redis_client
        self._ttl = ttl

    def _key(self, address: str) -> str:
        return f"wallet:{address}"

    def get(self, address: str) -> dict | None:
        import json

        raw = self._redis.get(self._key(address))
        if raw:
            return json.loads(raw)
        return None

    def set(self, address: str, data: dict) -> None:
        import json

        self._redis.setex(self._key(address), self._ttl, json.dumps(data))

    def invalidate(self, address: str) -> None:
        self._redis.delete(self._key(address))
